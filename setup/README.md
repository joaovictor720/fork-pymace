# Setup online do MACE

Execute na raiz do clone, como usuário normal:

```bash
./setup/setup.sh --batman emulated_wifi
```

Esse é o fluxo recomendado para os experimentos com Wi-Fi emulado. O script
instala os pacotes ausentes via `sudo apt`, baixa as dependências Python e o
CORE, compila o CORE e prepara o módulo BATMAN V com o patch do repositório.
Só a instalação de pacotes usa `sudo`. Pode haver uma pergunta do pacote
Wireshark sobre captura por usuários comuns; **Não** é suficiente para os
experimentos, cujas capturas são iniciadas como root nos namespaces.

Outras opções:

```bash
./setup/setup.sh                              # MACE básico, sem BATMAN
./setup/setup.sh --batman native              # BATMAN V upstream, sem patch
./setup/setup.sh --check --batman emulated_wifi # só mostra o plano
./setup/setup.sh --skip-system --batman emulated_wifi # pacotes já provisionados
./setup/doctor.sh --mode emulated_wifi        # diagnóstico sem alterar o host
```

`./install.sh` encaminha para esse mesmo setup. `--yes` aceita a instalação
dos pacotes pelo apt; não autoriza carregamento de módulos nem cadastro de chaves.
`--system-only` instala somente os pacotes.

## O que fica fixado e onde fica instalado

| Componente | Seleção e destino |
| --- | --- |
| Python | 3.12, ambiente `.venv/`; dependências em `requirements.txt` e `constraints.txt` |
| CORE | 9.2.0, commit em `versions.env`; biblioteca e `vcmd`/`vnoded` em `.venv/` |
| Fontes baixadas | `.build/sources/`, fora do Git; commits conferidos antes do build |
| BATMAN para kernel 6.8 | 2024.0, commit `7ee009fb21955bc7977d96b00eb8362a558d0d3a` |
| BATMAN para kernel 5.4 | 2019.4, commit `933568baeba83d6bcaa451656ec1550346f35996` |
| Patch/identidade BATMAN | `kernel/batman-adv-emulated-wifi/profile.sh` e `patches/` |
| Módulo emulado | `kernel/batman-adv-emulated-wifi/build/$(uname -r)/batman-adv.ko` |
| Módulo nativo | Mesmo diretório, subdiretório `native/` |
| Diagnóstico do setup | `.build/setup/diagnostic-<modo>.json` |

O setup básico não requer BATMAN, headers do kernel ou chaves. `emulated_wifi`
não exige compilar/assinar o nativo. Ambos os builds opcionais habilitam BATMAN V;
o módulo distribuído pelo Ubuntu deste computador não o habilita. Os arquivos
compilados ficam locais, sem instalação em `/lib/modules` ou substituição dos
módulos da distribuição. O setup nativo grava sua escolha local por kernel,
usada pelos runners; `BATADV_NATIVE_MODULE` explícito tem precedência.

Execute o mesmo comando para retomar: o setup reaproveita fontes verificadas,
dependências instaladas e módulos com metadados/hashes válidos, preservando a
assinatura. `--rebuild` força uma nova compilação de CORE e do módulo selecionado;
esse novo `.ko` precisa de nova assinatura. Mudanças no patch ou nos metadados
também invalidam a reutilização. Não edite o cache de fontes; alterações detectadas
interrompem o setup, em vez de serem descartadas. Não há DKMS nem atualização
automática do kernel. Os pacotes apt seguem a distribuição; o lock Python não
congela todo o sistema operacional.

## Assinatura e Secure Boot: etapa manual

O setup não altera confiança, carrega módulos, muda a rede ou reinicia a máquina.
Se os arquivos forem preparados mas houver checks pendentes, termina com código
**2** e informa as pendências. Falhas de instalação/compilação terminam com código
diferente de zero. Código **0** significa que os checks passaram, não que o
experimento em rede ou a confiança da assinatura já foram testados.

Com Secure Boot/lockdown exigindo assinatura, use uma chave cujo certificado
seja confiável para o kernel. Se você já tiver essa chave cadastrada, basta
assinar o arquivo:

```bash
./setup/sign-module.sh emulated_wifi /caminho/MOK.priv /caminho/MOK.der
./setup/setup.sh --batman emulated_wifi
```

Para `native`, troque o argumento nos dois comandos. A assinatura modifica
somente o artefato local e atualiza seus checksums. Não aplique `strip` depois
de assinar. As chaves privadas devem permanecer fora do repositório.

No Ubuntu, pode já existir um certificado cadastrado. Confira antes de criar
outra chave:

```bash
mokutil --test-key /var/lib/shim-signed/mok/MOK.der
```

Se esse certificado já estiver cadastrado e a chave privada correspondente for
administrada por root, assine usando sudo (comando inteiro em uma linha):

```bash
sudo ./setup/sign-module.sh emulated_wifi /var/lib/shim-signed/mok/MOK.priv /var/lib/shim-signed/mok/MOK.der
```

Reutilizar um certificado já confiável dispensa um novo cadastro MOK. A aceitação
da assinatura pelo kernel é confirmada no carregamento do módulo.

Caso precise criar e cadastrar uma chave neste Ubuntu, os comandos abaixo são
**para execução manual**. Não sobrescreva uma chave existente:

```bash
MACE_KEY_DIR="$HOME/.local/share/mace-module-signing"
mkdir -p "$MACE_KEY_DIR"
chmod 700 "$MACE_KEY_DIR"
(
  set -eu
  umask 077
  test ! -e "$MACE_KEY_DIR/MOK.priv"
  test ! -e "$MACE_KEY_DIR/MOK.der"
  openssl req -new -x509 -newkey rsa:2048 -nodes -days 3650 \
    -subj '/CN=MACE local module signing/' \
    -keyout "$MACE_KEY_DIR/MOK.priv" -outform DER -out "$MACE_KEY_DIR/MOK.der"
)
sudo mokutil --import "$MACE_KEY_DIR/MOK.der"
```

O cadastro normalmente exige reiniciar e confirmar **Enroll MOK** no gerenciador
de boot. Faça isso quando puder reiniciar. Depois, confira e assine:

```bash
MACE_KEY_DIR="$HOME/.local/share/mace-module-signing"
mokutil --test-key "$MACE_KEY_DIR/MOK.der"
./setup/sign-module.sh emulated_wifi "$MACE_KEY_DIR/MOK.priv" "$MACE_KEY_DIR/MOK.der"
./setup/setup.sh --batman emulated_wifi
```

A chave é cadastrada uma vez; cada novo build é assinado uma vez. O kernel
verifica a assinatura automaticamente a cada carga. O doctor detecta ausência
de assinatura, mas a presença dela não prova que a chave seja confiável.
Referências: [Secure Boot no Ubuntu](https://documentation.ubuntu.com/security/security-features/platform-protections/secure-boot/)
e [assinatura de módulos Linux](https://docs.kernel.org/admin-guide/module-signing.html).

## Execução e SSH

Após resolver as pendências, execute o smoke abaixo. O runner seleciona o módulo
do cenário usando sudo, configura o roteamento e cria os nós CORE. Para carregar
somente o módulo, use a primeira linha separadamente:

```bash
# Altera o kernel em execução: executar quando estiver pronto para o experimento.
kernel/batman-adv-emulated-wifi/module-control.sh ensure emulated_wifi
./evaluation/run_batman_wifi_smoke.sh
```

As aplicações C++ são compiladas automaticamente pelo runner. Para compilá-las
antecipadamente, use `./apps/crdt/build.sh all`; seus executáveis ficam ignorados
pelo Git. Depois do smoke, o ponto de entrada geral é
`./evaluation/run_scenario.sh <cenário> <aplicação> <run-id>`.

O controlador recusa trocar módulos desconhecidos ou em uso. Se um nativo já
estiver carregado, seu arquivo precisa estar disponível para permitir restauração
em caso de falha. Isso não exige que ele tenha BATMAN V para executar o modo
emulado. Veja os testes privilegiados e critérios de aceitação no
[relatório de portabilidade](../docs/portabilidade-ubuntu.md).

Para usar o build nativo diretamente com o controlador, fora dos runners:

```bash
export BATADV_NATIVE_MODULE="$PWD/kernel/batman-adv-emulated-wifi/build/$(uname -r)/native/batman-adv.ko"
kernel/batman-adv-emulated-wifi/module-control.sh ensure native
```

Na máquina remota, clone o repositório e execute o mesmo comando de setup via
SSH. Ela precisa de internet e de permissões para pacotes, namespaces e módulos.
Com Secure Boot, o cadastro inicial pode exigir console remoto/IPMI ou alguém
no console; acesso SSH com root não garante acesso ao gerenciador MOK no boot.

Esta estratégia não inclui bundle offline. Copiar somente o Git por SCP para
uma máquina sem internet não basta para um primeiro setup. Não copie `.venv`,
`.build` ou módulos compilados como ambiente portátil: eles dependem do caminho,
arquitetura, Python e kernel do destino. Recrie-os na máquina de destino.

## Limites do perfil atual

A receita automática de pacotes é para **Ubuntu 24.04**. Outras distribuições
podem usar `--skip-system`, com Python 3.12 (`MACE_SETUP_PYTHON` seleciona o
executável), compilador C/C++17, autotools/libtool/pkg-config/libev, Git,
iproute2, nftables, ethtool, tcpdump/tshark, nlohmann-json, netcat, ping e util-linux
instalados pelo administrador. Para BATMAN, também são necessários batctl,
kmod, OpenSSL, headers e `Module.symvers` correspondentes ao kernel em execução.
`mokutil` ajuda a diagnosticar Secure Boot em máquinas UEFI.

Os perfis BATMAN existentes cobrem as linhas 5.4 e 6.8; o build deste Ubuntu foi
validado em 6.8.0-139-generic/x86_64. Outros kernels exigem revisar compatibilidade
e acrescentar um perfil. Selecionar manualmente `BATADV_PROFILE` não comprova
compatibilidade. Este setup não instala PAPARAZZI, EMANE, OMNeT++ ou interface
gráfica do CORE. O caminho suportado aqui é a avaliação headless do MACE.
