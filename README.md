# homelab-k3s

Configuração do cluster k3s (3 masters + 5 workers Raspberry Pi, arm64).

| Peça | Função | Endereço |
|---|---|---|
| kube-vip | VIP da API (control plane) | 192.168.1.8:6443 |
| MetalLB | IPs dos Services LoadBalancer | pool 192.168.1.50–150 |
| Traefik | Ingress | 192.168.1.50 |
| Longhorn | Armazenamento persistente | UI em 192.168.1.51 |
| ServiceLB do k3s | **Desligado** (`disable: servicelb`) | — |

## Estrutura

```
apps/<namespace>/   objetos das aplicações, exportados do cluster
infra/              MetalLB, kube-vip, Longhorn, Traefik, StorageClasses
nodes/<host>/       config.yaml e serviço systemd de cada node (tokens ocultados)
VERSIONS.md         versões do k3s e das imagens no momento do backup
secrets-inventory.txt  nomes dos Secrets (o conteúdo NÃO está aqui)
```

## O que NÃO está neste repositório

Guardar à parte, num backup cifrado e fora do cluster:

- token do cluster: `sudo cat /var/lib/rancher/k3s/server/token` (num master)
- snapshots do etcd: `/var/lib/rancher/k3s/server/db/snapshots/`
- conteúdo dos Secrets
- `~/.kube/config`
- **dados dos volumes**: estão no Longhorn e precisam do backup do próprio Longhorn (Settings → Backup Target)

## Atualizar o backup

```
./backup-k3s.sh ~/homelab-k3s
cd ~/homelab-k3s
git diff                 # ver o que mudou no cluster
git add -A && git commit -m "Atualização $(date +%F)" && git push
```

Requisitos no laptop: `kubectl`, `git`, `yq` v4 (`sudo snap install yq`) e chave SSH nos nodes:

```
ssh-keygen -t ed25519            # se ainda não tiveres chave
for ip in 11 12 13 14 15 16 17 18; do ssh-copy-id pi@192.168.1.$ip; done
```

## Restauro

### Cenário A: o cluster existe mas perdi objetos
```
kubectl apply -f infra/metallb/
kubectl apply -f apps/<namespace>/
```

### Cenário B: reconstrução de raiz

1. **Instalar o k3s** no primeiro master, com as mesmas flags que estão em `nodes/pi-master-1/systemd-service.txt` e o mesmo `config.yaml`, usando a versão de `VERSIONS.md`.
2. **Restaurar o etcd** (opcional, se tiveres snapshot) em vez de reaplicar tudo:
   ```
   sudo k3s server --cluster-reset \
     --cluster-reset-restore-path=<snapshot> \
     --token=<token-guardado>
   ```
3. **Juntar os outros masters e os workers** com as flags de `nodes/<host>/`.
4. **Instalar o kube-vip**: `kubectl apply -f infra/kube-vip/`
5. **Instalar o MetalLB** com o manifesto oficial da versão em `VERSIONS.md`, depois `kubectl apply -f infra/metallb/`.
6. **Instalar o Longhorn** com o manifesto oficial da versão em `VERSIONS.md`, depois `kubectl apply -f infra/longhorn/` e restaurar os volumes a partir do Backup Target.
7. **Recriar os Secrets** a partir do backup cifrado (ver `secrets-inventory.txt`).
8. **Aplicar as apps**: `kubectl apply -f apps/ -R`

Notas:
- Os Services LoadBalancer têm a anotação `metallb.universe.tf/loadBalancerIPs` com o IP atual, para manterem o mesmo IP depois do restauro.
- Os PVCs foram exportados sem `volumeName`: num cluster novo, o Longhorn cria volumes novos. Para recuperar os dados, restaura primeiro o volume a partir do backup do Longhorn com o mesmo nome do PVC.

## Próximo passo

Passar a GitOps com Flux (`flux bootstrap gitea`), para o cluster se sincronizar sozinho a partir deste repositório.
