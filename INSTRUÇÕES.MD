# Contexto e Objetivo
Desenvolver uma aplicação web em Streamlit (Python) chamada "Auditoria Vértice", projetada para automatizar o cruzamento de extratos bancários (OFX/CSV) e os lançamentos do ERP Omie, auditando taxas de boletos, custos de antecipação de recebíveis, conformidade de contratos de empréstimos e permitindo o cadastro dinâmico de novas regras de auditoria pelo usuário.

# Stack Tecnológica Obrigatória
- **Linguagem:** Python 3.10+
- **Interface:** Streamlit (com componentes limpos, respiro visual e sem poluição)
- **Motor Analítico & Queries:** DuckDB (processamento em memória de alta performance) e Pandas
- **Validação de Dados:** Pandera
- **Identidade Visual (Design System Vértice):**
  - Tipografia: Títulos em `Cinzel` (maiúsculas, espaçamento elegante), Corpo em `Outfit`.
  - Paleta de Cores: Bege Linho (#EFEBE4), Areia (#DCD3C6), Cinza Grafite (#2C3531), Verde Sálvia (#7A8B7A), Dourado Âmbar (#C5A059) e alertas em tons funcionais de destaque.

---

## Módulos da Aplicação (Arquitetura de Telas no Streamlit)

### 1. Aba de Ingestão e Conexão (ETL)
- **Upload de Extratos:** Área de arrastar e soltar (Dropzone) para arquivos `.OFX` e `.CSV` de múltiplos bancos.
- **Importação Omie:** Conexão via API ou upload de relatórios exportados (Movimento de Conta Corrente, Contas a Pagar/Receber, Baixas).
- **Normalização Automática:** Limpeza de descrições, conversão de datas (AAAA-MM-DD) e padronização numérica.

### 2. Aba de Regras de Auditoria & Cadastro Dinâmico
- **Catálogo Padrão Ativo:**
  - *AUD-01:* Lançamentos no Omie sem correspondência no extrato (Órfãos).
  - *AUD-02:* Lançamentos no extrato sem baixa no ERP (Fantasma).
  - *AUD-03:* Divergência em Tarifas de Boletos (Valor cobrado > Tarifa de contrato).
  - *AUD-04:* Divergência de Taxas de Antecipação de Recebíveis (Spread oculto / erro de cálculo).
  - *AUD-05:* Divergência em parcelas de Empréstimos (Juros pagos vs. Tabela Price/SAC teórica).
- **Construtor de Regras Customizadas (No-Code/Low-Code):**
  - Interface onde o usuário define: Nome da Regra, Descrição, Condição SQL (ex: `ABS(e.valor - o.valor) > 10.0`), Severidade (Crítico, Alerta, Informativo) e Categoria. As regras criadas são salvas em um arquivo JSON local ou tabela DuckDB persistente.

### 3. Aba de Execução & Matriz de Divergências
- Botão único: **"Executar Auditoria Completa"**.
- Exibição de **KPIs Executivos**: Valor Total Divergente (R$), Quantidade de Alertos Críticos, Percentual de Aderência do ERP.
- **Tabela Interativa de Exceções:** Filtros por severidade, banco e tipo de divergência, com opção de exportação para Excel/CSV auditável.
- **Drill-Down:** Clicar em uma linha mostra o detalhe exato do lançamento no extrato e o correspondente (ou ausente) no Omie.

---

## Especificações Técnicas de Cálculo (Backend)

### A. Auditoria de Boletos
Para cada linha do extrato identificada como liquidação de boleto:
`Tarifa Cobrada = Valor Nominal (Extrato) - Valor Líquido Creditado (Extrato)`
Se `Tarifa Cobrada > Tarifa Contratada (cadastrada por banco)`, gerar divergência com o delta a maior.

### B. Auditoria de Antecipações
Para cada lote antecipado:
Recalcular o Valor Presente (VP) teórico usando a taxa diária/mensal contratada sobre o valor nominal e prazo original.
Comparar com o valor líquido creditado pelo banco. Diferenças superiores a R$ 0,10 por centavo de arredondamento devem ser sinalizadas como "Desconto Indevido de Antecipação".

### C. Auditoria de Empréstimos (Price / SAC)
Gerar a matriz de parcelas teóricas a partir dos parâmetros do contrato (Principal, Taxa, Prazo, Sistema).
Cruzamento mensal com as baixas efetivas no Omie para garantir que o principal reduziu o passivo e os juros foram para a despesa financeira correta.