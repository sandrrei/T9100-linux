// SPDX-License-Identifier: GPL-2.0-only
/*
 * Goodix GT827 touchscreen driver (GT82x "Guitar" family)
 *
 * Protocol recovered from the GPL vendor driver "gt82x" (Goodix "GTP Series
 * Driver", Allwinner Android kernel 3.0) and verified on an Allwinner A13
 * tablet with i2c-tools:
 *
 *   - registers have 16-bit big-endian addresses
 *   - after every access the host writes the "end command" (register 0x8000,
 *     no data); without it the controller does not prepare new data
 *   - 0x0F7D: 3 bytes, product id (0x27 = GT827) and firmware version
 *   - 0x0F80: 112-byte configuration, kept in RAM only (lost on reset), so it
 *     is sent at every probe; the controller asks for it again by setting the
 *     low nibble of 0x0F41 to 0xF
 *   - 0x0F40: touch data
 *       byte 0: bit 7 = data valid, bits 0-4 = mask of active contact ids
 *       byte 1: low nibble 0xF = configuration requested
 *       then 5 bytes per active contact, packed in id order:
 *       X (be16), Y (be16), size
 *
 * Coordinates are in the sensor's own orientation (480x800 portrait on the
 * tested tablet); rotation is handled with the standard touchscreen-* device
 * tree properties.
 */

#include <linux/bitops.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/input.h>
#include <linux/input/mt.h>
#include <linux/input/touchscreen.h>
#include <linux/interrupt.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/unaligned.h>

#define GT827_REG_TOUCH		0x0f40
#define GT827_REG_VERSION	0x0f7d
#define GT827_REG_CONFIG	0x0f80
#define GT827_REG_END_CMD	0x8000

#define GT827_MAX_CONTACTS	5
#define GT827_CONTACT_SIZE	5
#define GT827_TOUCH_LEN		(2 + GT827_MAX_CONTACTS * GT827_CONTACT_SIZE)
#define GT827_CONFIG_LEN	112
#define GT827_CONFIG_RETRIES	5

#define GT827_DEFAULT_SIZE_X	480
#define GT827_DEFAULT_SIZE_Y	800

static bool poll;
module_param(poll, bool, 0444);
MODULE_PARM_DESC(poll, "Poll the controller instead of using its interrupt");

/*
 * Default configuration from the vendor driver (sensor 480x800, interrupt on
 * rising edge). Bytes 69-72 hold the X and Y resolution (be16).
 */
static const u8 gt827_default_config[GT827_CONFIG_LEN] = {
	0x00, 0x0f, 0x01, 0x10, 0x02, 0x11, 0x03, 0x12,
	0x04, 0x13, 0x05, 0x14, 0x06, 0x15, 0x07, 0x16,
	0x08, 0x17, 0x09, 0x18, 0x0a, 0x19, 0x0b, 0x1a,
	0x0c, 0x1b, 0x0d, 0x1c, 0xff, 0xff, 0x02, 0x0c,
	0x03, 0x0d, 0x04, 0x0e, 0x05, 0x0f, 0x06, 0x10,
	0x07, 0x11, 0x08, 0x12, 0x09, 0x13, 0xff, 0xff,
	0xff, 0xff, 0x1f, 0x03, 0x88, 0x00, 0x00, 0x2d,
	0x00, 0x00, 0x07, 0x00, 0x00, 0x0a, 0x6a, 0x55,
	0x1d, 0x03, 0x00, 0x05, 0x00, 0x01, 0xe0, 0x03,
	0x20, 0x41, 0x40, 0x43, 0x43, 0x28, 0x00, 0x05,
	0x19, 0x02, 0x14, 0x10, 0x00, 0x02, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00, 0x50, 0x00, 0x60, 0x20,
	0x00, 0x00, 0x00, 0x00, 0x09, 0x88, 0x2c, 0x00,
	0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
};

struct gt827_ts {
	struct i2c_client *client;
	struct input_dev *input;
	struct gpio_desc *reset;
	struct touchscreen_properties prop;
};

static int gt827_read(struct i2c_client *client, u16 reg, u8 *buf, int len)
{
	u8 addr[2];
	struct i2c_msg msgs[2] = {
		{ .addr = client->addr, .flags = 0,        .len = 2,   .buf = addr },
		{ .addr = client->addr, .flags = I2C_M_RD, .len = len, .buf = buf  },
	};
	int ret;

	put_unaligned_be16(reg, addr);
	ret = i2c_transfer(client->adapter, msgs, ARRAY_SIZE(msgs));
	if (ret == ARRAY_SIZE(msgs))
		return 0;
	return ret < 0 ? ret : -EIO;
}

static int gt827_write(struct i2c_client *client, u16 reg, const u8 *data, int len)
{
	u8 *buf;
	int ret;

	buf = kmalloc(len + 2, GFP_KERNEL);
	if (!buf)
		return -ENOMEM;

	put_unaligned_be16(reg, buf);
	if (len)
		memcpy(buf + 2, data, len);

	ret = i2c_master_send(client, buf, len + 2);
	kfree(buf);
	if (ret == len + 2)
		return 0;
	return ret < 0 ? ret : -EIO;
}

static void gt827_end_cmd(struct i2c_client *client)
{
	gt827_write(client, GT827_REG_END_CMD, NULL, 0);
}

static int gt827_send_config(struct gt827_ts *ts)
{
	int error = 0;
	int i;

	for (i = 0; i < GT827_CONFIG_RETRIES; i++) {
		error = gt827_write(ts->client, GT827_REG_CONFIG,
				    gt827_default_config, GT827_CONFIG_LEN);
		gt827_end_cmd(ts->client);
		if (!error)
			break;
		msleep(10);
	}
	msleep(10);
	return error;
}

static void gt827_reset(struct gt827_ts *ts)
{
	if (!ts->reset)
		return;

	gpiod_set_value_cansleep(ts->reset, 1);		/* assert (line low) */
	msleep(20);
	gpiod_set_value_cansleep(ts->reset, 0);		/* release */
	msleep(80);
}

static void gt827_process(struct gt827_ts *ts)
{
	u8 data[GT827_TOUCH_LEN];
	unsigned long mask;
	int error, id, n = 0;

	error = gt827_read(ts->client, GT827_REG_TOUCH, data, sizeof(data));
	gt827_end_cmd(ts->client);
	if (error) {
		dev_err_ratelimited(&ts->client->dev,
				    "failed to read touch data: %d\n", error);
		return;
	}

	if ((data[1] & 0x0f) == 0x0f) {
		dev_info(&ts->client->dev, "controller requested its configuration\n");
		gt827_send_config(ts);
		return;
	}

	if ((data[0] & 0xc0) != 0x80)
		return;

	mask = data[0] & GENMASK(GT827_MAX_CONTACTS - 1, 0);
	for_each_set_bit(id, &mask, GT827_MAX_CONTACTS) {
		const u8 *c = &data[2 + n * GT827_CONTACT_SIZE];

		input_mt_slot(ts->input, id);
		input_mt_report_slot_state(ts->input, MT_TOOL_FINGER, true);
		touchscreen_report_pos(ts->input, &ts->prop,
				       get_unaligned_be16(&c[0]),
				       get_unaligned_be16(&c[2]), true);
		input_report_abs(ts->input, ABS_MT_TOUCH_MAJOR, c[4]);
		n++;
	}

	input_mt_sync_frame(ts->input);
	input_sync(ts->input);
}

static irqreturn_t gt827_irq(int irq, void *dev_id)
{
	gt827_process(dev_id);
	return IRQ_HANDLED;
}

static void gt827_poll(struct input_dev *input)
{
	gt827_process(input_get_drvdata(input));
}

static int gt827_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct gt827_ts *ts;
	u8 version[3];
	int error;

	if (!i2c_check_functionality(client->adapter, I2C_FUNC_I2C))
		return -ENXIO;

	ts = devm_kzalloc(dev, sizeof(*ts), GFP_KERNEL);
	if (!ts)
		return -ENOMEM;
	ts->client = client;
	i2c_set_clientdata(client, ts);

	/* Requested asserted, so the controller always starts from a clean reset */
	ts->reset = devm_gpiod_get_optional(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(ts->reset))
		return dev_err_probe(dev, PTR_ERR(ts->reset), "failed to get reset GPIO\n");
	gt827_reset(ts);

	error = gt827_read(client, GT827_REG_VERSION, version, sizeof(version));
	gt827_end_cmd(client);
	if (error)
		return dev_err_probe(dev, error, "failed to read version\n");
	dev_info(dev, "GT8%02x, firmware %02x%02x\n", version[0], version[1], version[2]);

	error = gt827_send_config(ts);
	if (error)
		return dev_err_probe(dev, error, "failed to send configuration\n");

	ts->input = devm_input_allocate_device(dev);
	if (!ts->input)
		return -ENOMEM;

	ts->input->name = "Goodix GT827 Touchscreen";
	ts->input->phys = "input/ts";
	ts->input->id.bustype = BUS_I2C;
	input_set_drvdata(ts->input, ts);

	input_set_abs_params(ts->input, ABS_MT_POSITION_X, 0, GT827_DEFAULT_SIZE_X - 1, 0, 0);
	input_set_abs_params(ts->input, ABS_MT_POSITION_Y, 0, GT827_DEFAULT_SIZE_Y - 1, 0, 0);
	input_set_abs_params(ts->input, ABS_MT_TOUCH_MAJOR, 0, 255, 0, 0);
	touchscreen_parse_properties(ts->input, true, &ts->prop);

	error = input_mt_init_slots(ts->input, GT827_MAX_CONTACTS,
				    INPUT_MT_DIRECT | INPUT_MT_DROP_UNUSED);
	if (error)
		return dev_err_probe(dev, error, "failed to init MT slots\n");

	if (poll || client->irq <= 0) {
		error = input_setup_polling(ts->input, gt827_poll);
		if (error)
			return dev_err_probe(dev, error, "failed to set up polling\n");
		input_set_poll_interval(ts->input, 20);
		dev_info(dev, "polling every 20 ms\n");
	}

	error = input_register_device(ts->input);
	if (error)
		return dev_err_probe(dev, error, "failed to register input device\n");

	if (!poll && client->irq > 0) {
		/* trigger type comes from the device tree */
		error = devm_request_threaded_irq(dev, client->irq, NULL, gt827_irq,
						  IRQF_ONESHOT, client->name, ts);
		if (error)
			return dev_err_probe(dev, error, "failed to request IRQ\n");
	}

	return 0;
}

static const struct i2c_device_id gt827_id[] = {
	{ "gt827" },
	{ }
};
MODULE_DEVICE_TABLE(i2c, gt827_id);

static const struct of_device_id gt827_of_match[] = {
	{ .compatible = "goodix,gt827" },
	{ }
};
MODULE_DEVICE_TABLE(of, gt827_of_match);

static struct i2c_driver gt827_driver = {
	.driver = {
		.name = "gt827",
		.of_match_table = gt827_of_match,
	},
	.probe = gt827_probe,
	.id_table = gt827_id,
};
module_i2c_driver(gt827_driver);

MODULE_DESCRIPTION("Goodix GT827 touchscreen driver");
MODULE_AUTHOR("Sandro <sandrrei@gmail.com>");
MODULE_LICENSE("GPL");
