# Portabilidade do MACE: suporte e validação

O procedimento de instalação, assinatura e execução está em
[setup/README.md](../setup/README.md). Este documento registra o que foi adaptado,
o que foi validado e os limites do perfil disponível.

## Ambiente validado

| Componente | Perfil |
| --- | --- |
| Sistema | Ubuntu 24.04, x86_64, máquina física |
| Kernel testado | `6.8.0-139-generic` |
| Python / CORE | Python 3.12 / CORE 9.2.0, instalados em `.venv/` |
| BATMAN emulado | Upstream 2024.0 + patch local, versão `2024.0-macewifi1` |
| BATMAN nativo opcional | Upstream 2024.0 sem patch, versão `2024.0-macev1` |
| Secure Boot | Habilitado durante o teste real de `emulated_wifi` |
| Execução | CLI headless, sem PAPARAZZI, EMANE ou OMNeT++ |

O setup fixa os commits de CORE/BATMAN e as versões Python. Os pacotes do sistema
seguem os repositórios da distribuição. O perfil BATMAN 5.4/2019.4 continua
preservado para a VM; outros kernels precisam de revisão e de um perfil adequado.
A instalação automática de pacotes suporta Ubuntu 24.04. Em outras distribuições,
`--skip-system` exige dependências instaladas pelo administrador e não representa
uma certificação de compatibilidade.

## Adaptações necessárias

- **Instalação local:** CORE, ferramentas `vcmd`/`vnoded` e dependências Python
  ficam no checkout. O setup baixa fontes fixadas, compila sem root e permite
  retomar a preparação. BATMAN é opcional para cenários IP.
- **Compatibilidade CORE:** criação de nós e posições compatível com as APIs
  8.2/9.2, tratamento de erros de serviços e suporte à ausência de `write_nodes`.
  A limpeza encerra as sessões criadas pelo MACE; não chama `core-cleanup` global.
- **Execução por SSH:** interface web opcional via `--web`, importação de PAPARAZZI
  apenas quando solicitado, usuário e diretórios derivados da execução atual.
- **Python e caminhos:** os runners preservam explicitamente o interpretador e
  o ambiente necessários através do sudo e dos nós. A geração de comandos e a
  injeção de caminhos respeitam os formatos de shell e JSON. Regere `mace.json`
  antigos depois de mover o checkout ou preparar outra máquina.
- **Aplicações C++:** compiladas em cada host; os executáveis gerados não são
  versionados. O reuso exige fontes, compilador, arquitetura, flags e conteúdo do
  binário correspondentes. O runner compila a aplicação antes de executá-la.
- **BATMAN:** perfis 5.4/6.8, BATMAN V habilitado no build, suporte a módulos nativos
  comprimidos, artefatos separados para native/emulated_wifi e hashes atualizados
  após assinatura. O modo emulado não depende de preparar o nativo.
- **Roteamento:** a configuração é síncrona antes das aplicações. A checagem do
  algoritmo lê `/sys/module/batman_adv/parameters/routing_algo`, evitando interpretar
  o relatório formatado do `batctl` ou exigir root apenas para coletar esse valor.
- **Coleta:** os resultados incluem configuração, ambiente, versões Python e
  metadados do módulo. Falhas do MACE são propagadas ao runner.

A captura de tráfego e o GPS de GCounter agora incluem os 30 segundos de espera
inicial. Antes, a janela padrão podia terminar antes de a aplicação transmitir.
**Essa mudança afeta os intervalos de medição:** revise capturas e janelas antes de
comparar resultados anteriores com os produzidos pelos runners atualizados.
O cenário `batman_wifi_smoke` usa mobilidade `none`; random waypoint com velocidade
zero provocava divisão por zero.

## Validação realizada

Em 24/09/2026, a execução `batman_wifi_smoke/broadcast/20260924T163107Z` passou no
Ubuntu físico com o módulo assinado e Secure Boot ativo. Foram verificados:

- Dois nós executando a aplicação com código de saída zero.
- Tráfego UDP de aplicação nas duas capturas e amostras GPS nos dois nós.
- Sequências BATADV_BCAST capturadas três vezes, conforme o comportamento emulado.
- 583 quadros por captura, sendo 45 de aplicação e 538 de controle; nenhum descarte
  reportado pelo tcpdump.

As evidências completas permanecem em `results/`, fora do Git. As suítes de
regressão Python e C++ e os builds nativo/emulado também foram executados durante
esta migração. A assinatura usou um certificado já cadastrado nesse host; não foi
necessário novo cadastro MOK nem reboot.

Para repetir o smoke de rede, na raiz do checkout preparado:

```bash
./evaluation/run_batman_wifi_smoke.sh
```

Esse comando usa sudo, configura o BATMAN e cria redes CORE temporárias. O teste
privilegiado isolado do protocolo, para comparação native/emulated_wifi, permanece
em `kernel/batman-adv-emulated-wifi/smoke-test.sh`; seu escopo é diferente do smoke
MACE acima. Para um cenário IP sem BATMAN:

```bash
./evaluation/run_scenario.sh ip_smoke broadcast ip_001
./.venv/bin/python evaluation/validate_smoke.py results/ip_smoke/broadcast/ip_001
```

Para verificar o código sem iniciar redes ou carregar módulos:

```bash
./.venv/bin/python -m pytest evaluation/tests -q
./apps/crdt/build.sh test
```

## Limites da evidência

O smoke aprovado cobre dois nós estáticos em broadcast com `emulated_wifi`.
Comparação sistemática com a VM, cenários maiores, mobilidade e todas as políticas
precisam de validação experimental própria. O build nativo passou na compilação;
a comparação de comportamento native/emulated_wifi no Ubuntu ainda está pendente.

A migração mudou CORE 8.2 para 9.2, Python 3.8 para 3.12 e BATMAN 2019.4 para 2024.0.
Preservar a intenção do experimento não demonstra equivalência numérica entre os
ambientes. As assinaturas e os binários são preparados por máquina/kernel; não
são parte portátil do clone. O setup atual requer internet e não inclui DKMS ou
bundle offline. Cadastro inicial MOK por SSH pode exigir console remoto no boot.
