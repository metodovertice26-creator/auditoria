"""Gerador de arquivos sintéticos realistas (OFX, CSV Bancário e Relatório Omie XLSX/CSV)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "dados_exemplo"


def ensure_sample_files(target_dir: Path = SAMPLE_DIR) -> dict[str, Path]:
    """Cria arquivos realistas em dados_exemplo/ cobrindo os cenários AUD-01 a AUD-05."""
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Extrato OFX 1.x SGML - Itaú Unibanco (COMPE 0341)
    # Contém:
    # - PIX recebido conciliado (R$ 18.500,00)
    # - Liquidação de Boleto DOC BOL-8801: Nominal no Omie R$ 4.200,00, Líquido OFX R$ 4.193,10 (Tarifa R$ 6,90 vs R$ 2,45 contratado -> AUD-03!)
    # - Débito avulso de Tarifa de Cobrança QTDE 10 = -R$ 59,00 (R$ 5,90/boleto vs R$ 2,45 -> AUD-03 + AUD-02 fantasma se não lançado no Omie!)
    # - Antecipação de Recebíveis Lote ANT-301: VN 60000,00 PRAZO 45D, Líquido R$ 57.420,00 (Taxa contratada 1,85% a.m. composto -> VP Teórico R$ 58.372,62 -> AUD-04!)
    # - Parcela 3/12 Capital de Giro #9941 (Price 120k, 1,65% a.m., 12x -> PMT teórica R$ 11.105,60; pago R$ 11.105,60, mas no Omie 100% foi jogado em Despesa Financeira sem baixar Passivo -> AUD-05!)
    # - Pagamento Fornecedor PIX conciliado (-R$ 6.450,00)
    # - Débito Fantasma no Extrato (-R$ 489,90 Pacote Empresarial não baixado no Omie -> AUD-02!)
    ofx_itau = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1>
<SONRS>
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<DTSERVER>20261031180000[-3:BRT]
<LANGUAGE>POR
<FI>
<ORG>ITAU UNIBANCO S.A.
<FID>0341
</FI>
</SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>1001
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<STMTRS>
<CURDEF>BRL
<BANKACCTFROM>
<BANKID>0341
<BRANCHID>1245
<ACCTID>45892-7
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20261001000000[-3:BRT]
<DTEND>20261031235959[-3:BRT]
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261005101500[-3:BRT]
<TRNAMT>18500.00
<FITID>ITAU20261005001
<REFNUM>NF-5012
<MEMO>PIX RECEBIDO CLIENTE HORIZONTE COMERCIO NF-5012
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261008112000[-3:BRT]
<TRNAMT>4193.10
<FITID>BOL-8801
<CHECKNUM>BOL-8801
<MEMO>LIQUIDACAO COBRANCA BOLETO BOL-8801 VN 4200,00
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261009080000[-3:BRT]
<TRNAMT>-59.00
<FITID>TAR2026100901
<MEMO>TARIFA LIQUIDACAO COBRANCA QTDE 10
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261013153000[-3:BRT]
<TRNAMT>57420.00
<FITID>ANT-301
<REFNUM>ANT-301
<MEMO>CREDITO ANTECIPACAO RECEBIVEIS LOTE ANT-301 VN 60000,00 PRAZO 45D
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261015090000[-3:BRT]
<TRNAMT>-11105.60
<FITID>CTR9941-P03
<REFNUM>CTR9941-P03
<MEMO>DEBITO PARCELA CAP GIRO CONTRATO 9941 PARC 03/12
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261018141000[-3:BRT]
<TRNAMT>-6450.00
<FITID>PAG-7710
<REFNUM>PAG-7710
<MEMO>PIX ENVIADO FORNECEDOR METALURGICA SUL PAG-7710
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261025073000[-3:BRT]
<TRNAMT>-489.90
<FITID>ITAU-TAR-992
<MEMO>TARIFA PACOTE EMPRESARIAL MAX NAO CONCILIADO
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>142508.60
<DTASOF>20261031180000[-3:BRT]
</LEDGERBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""
    ofx_path = target_dir / "extrato_itau_out2026.ofx"
    ofx_path.write_text(ofx_itau, encoding="cp1252")

    # 2. Extrato CSV Bancário - Bradesco (COMPE 237)
    # Contém:
    # - Liquidação de Boleto BOL-9102: Nominal R$ 8.500,00, Líquido R$ 8.494,20 (Tarifa R$ 5,80 vs R$ 2,30 contratada -> AUD-03)
    # - Parcela 2/18 Financiamento SAC #4410 em 20/10/2026:
    #   SAC Principal 180.000, 18x -> Amort = 10.000,00. Saldo após P1 = 170.000. Juros P2 (1,40%) = 2.380,00 -> PMT teórica = 12.380,00.
    #   Porém banco/Omie cobrou R$ 12.790,00 (Juros cobrados a maior de +R$ 410,00 -> AUD-05!)
    # - Recebimento TED Cliente Alfa R$ 22.000,00 conciliado
    csv_bradesco = """Extrato de Conta Corrente - Bradesco Empresas;;;;;;;
Agência: 0412 | Conta: 99812-3 | Período: Outubro/2026;;;;;;;
Data;Documento;Histórico;Valor (R$);Valor Nominal;Valor Líquido;Prazo Dias;Banco
06/10/2026;TED-4010;TED RECEBIDA CLIENTE ALFA LOGISTICA;22.000,00;22.000,00;22.000,00;;Bradesco
11/10/2026;BOL-9102;LIQUIDACAO COBRANCA BOLETO BOL-9102;8.494,20;8.500,00;8.494,20;;Bradesco
16/10/2026;LOTE-PIX-88;CREDITO PIX AGRUPADO CLIENTES VAREJO;5.400,00;5.400,00;5.400,00;;Bradesco
20/10/2026;CTR4410-P02;DEBITO FINANCIAMENTO EXPANSAO CONTRATO 4410 PARC 02/18;-12.790,00;;;;Bradesco
"""
    csv_bank_path = target_dir / "extrato_bradesco_out2026.csv"
    csv_bank_path.write_text(csv_bradesco, encoding="utf-8-sig")

    # 3. Relatório Exportado Omie (XLSX + CSV)
    omie_rows = [
        {
            "Código Lançamento": "OM-1001",
            "Data da Baixa": "05/10/2026",
            "Data de Vencimento": "05/10/2026",
            "Situação": "Conciliado",
            "Tipo": "Entrada",
            "Banco": "Itaú Unibanco",
            "Conta Corrente": "45892-7",
            "Categoria": "1.01.01 - Receita de Vendas de Mercadorias",
            "Cliente / Fornecedor": "Horizonte Comércio Ltda",
            "Número do Documento": "NF-5012",
            "Observações": "Recebimento PIX NF-5012",
            "Valor Líquido": 18500.00,
            "Valor Nominal": 18500.00,
            "Juros": 0.00,
            "Desconto": 0.00,
            "Contrato": "",
        },
        {
            "Código Lançamento": "OM-1002",
            "Data da Baixa": "08/10/2026",
            "Data de Vencimento": "08/10/2026",
            "Situação": "Baixado",
            "Tipo": "Entrada",
            "Banco": "Itaú Unibanco",
            "Conta Corrente": "45892-7",
            "Categoria": "1.01.01 - Receita de Vendas de Mercadorias",
            "Cliente / Fornecedor": "Distribuidora Atlântica",
            "Número do Documento": "BOL-8801",
            "Observações": "Baixa de boleto cobrado pelo valor cheio no ERP",
            "Valor Líquido": 4200.00,
            "Valor Nominal": 4200.00,
            "Juros": 0.00,
            "Desconto": 0.00,
            "Contrato": "",
        },
        {
            "Código Lançamento": "OM-1003",
            "Data da Baixa": "13/10/2026",
            "Data de Vencimento": "27/11/2026",
            "Situação": "Baixado",
            "Tipo": "Entrada",
            "Banco": "Itaú Unibanco",
            "Conta Corrente": "45892-7",
            "Categoria": "1.01.02 - Antecipação de Recebíveis",
            "Cliente / Fornecedor": "Carteira Duplicatas Outubro",
            "Número do Documento": "ANT-301",
            "Observações": "Baixa lote antecipação ANT-301",
            "Valor Líquido": 57420.00,
            "Valor Nominal": 60000.00,
            "Juros": 0.00,
            "Desconto": 2580.00,
            "Contrato": "ANT-301",
        },
        {
            # Erro contábil clássico para AUD-05: 100% da parcela Price (11.105,60) lançada como Despesa Financeira de Juros, sem reduzir Passivo!
            "Código Lançamento": "OM-1004",
            "Data da Baixa": "15/10/2026",
            "Data de Vencimento": "15/10/2026",
            "Situação": "Baixado",
            "Tipo": "Saída",
            "Banco": "Itaú Unibanco",
            "Conta Corrente": "45892-7",
            "Categoria": "2.04.02 - Despesa Financeira - Juros de Empréstimos",
            "Cliente / Fornecedor": "Itaú Unibanco S.A.",
            "Número do Documento": "CTR9941-P03",
            "Observações": "Pagamento parcela 03/12 Capital de Giro Contrato 9941 lançado integralmente em despesa",
            "Valor Líquido": -11105.60,
            "Valor Nominal": 11105.60,
            "Juros": 11105.60,
            "Desconto": 0.00,
            "Contrato": "CTR-GIRO-341",
        },
        {
            "Código Lançamento": "OM-1005",
            "Data da Baixa": "18/10/2026",
            "Data de Vencimento": "18/10/2026",
            "Situação": "Conciliado",
            "Tipo": "Saída",
            "Banco": "Itaú Unibanco",
            "Conta Corrente": "45892-7",
            "Categoria": "2.01.03 - Matéria-Prima e Insumos",
            "Cliente / Fornecedor": "Metalúrgica Sul S.A.",
            "Número do Documento": "PAG-7710",
            "Observações": "Pagamento fornecedor PIX PAG-7710",
            "Valor Líquido": -6450.00,
            "Valor Nominal": 6450.00,
            "Juros": 0.00,
            "Desconto": 0.00,
            "Contrato": "",
        },
        {
            # Lançamento ÓRFÃO no Omie (AUD-01): baixado manualmente no ERP mas nunca saiu do banco!
            "Código Lançamento": "OM-1006",
            "Data da Baixa": "22/10/2026",
            "Data de Vencimento": "22/10/2026",
            "Situação": "Baixado",
            "Tipo": "Saída",
            "Banco": "Itaú Unibanco",
            "Conta Corrente": "45892-7",
            "Categoria": "2.02.05 - Serviços de Consultoria",
            "Cliente / Fornecedor": "Vektor Assessoria Empresarial",
            "Número do Documento": "NFSE-319",
            "Observações": "Baixa manual indevida sem pagamento bancário efetivo",
            "Valor Líquido": -3850.00,
            "Valor Nominal": 3850.00,
            "Juros": 0.00,
            "Desconto": 0.00,
            "Contrato": "",
        },
        {
            "Código Lançamento": "OM-2001",
            "Data da Baixa": "06/10/2026",
            "Data de Vencimento": "06/10/2026",
            "Situação": "Conciliado",
            "Tipo": "Entrada",
            "Banco": "Bradesco",
            "Conta Corrente": "99812-3",
            "Categoria": "1.01.01 - Receita de Prestação de Serviços",
            "Cliente / Fornecedor": "Alfa Logística Integrada",
            "Número do Documento": "TED-4010",
            "Observações": "Recebimento TED Cliente Alfa",
            "Valor Líquido": 22000.00,
            "Valor Nominal": 22000.00,
            "Juros": 0.00,
            "Desconto": 0.00,
            "Contrato": "",
        },
        {
            "Código Lançamento": "OM-2002",
            "Data da Baixa": "11/10/2026",
            "Data de Vencimento": "11/10/2026",
            "Situação": "Baixado",
            "Tipo": "Entrada",
            "Banco": "Bradesco",
            "Conta Corrente": "99812-3",
            "Categoria": "1.01.01 - Receita de Vendas de Mercadorias",
            "Cliente / Fornecedor": "Comercial Nova Era",
            "Número do Documento": "BOL-9102",
            "Observações": "Liquidação boleto BOL-9102",
            "Valor Líquido": 8494.20,
            "Valor Nominal": 8500.00,
            "Juros": 0.00,
            "Desconto": 5.80,
            "Contrato": "",
        },
        {
            # Exemplo 1:N Subset Sum: 2 títulos baixados no Omie (3.100 + 2.300 = 5.400) que caíram num único crédito no Bradesco
            "Código Lançamento": "OM-2004A",
            "Data da Baixa": "16/10/2026",
            "Data de Vencimento": "16/10/2026",
            "Situação": "Baixado",
            "Tipo": "Entrada",
            "Banco": "Bradesco",
            "Conta Corrente": "99812-3",
            "Categoria": "1.01.01 - Receita de Vendas de Mercadorias",
            "Cliente / Fornecedor": "Varejo Sul Ltda",
            "Número do Documento": "NF-810",
            "Observações": "Recebimento NF-810 integrante do lote PIX",
            "Valor Líquido": 3100.00,
            "Valor Nominal": 3100.00,
            "Juros": 0.00,
            "Desconto": 0.00,
            "Contrato": "",
        },
        {
            "Código Lançamento": "OM-2004B",
            "Data da Baixa": "16/10/2026",
            "Data de Vencimento": "16/10/2026",
            "Situação": "Baixado",
            "Tipo": "Entrada",
            "Banco": "Bradesco",
            "Conta Corrente": "99812-3",
            "Categoria": "1.01.01 - Receita de Vendas de Mercadorias",
            "Cliente / Fornecedor": "Varejo Norte Ltda",
            "Número do Documento": "NF-811",
            "Observações": "Recebimento NF-811 integrante do lote PIX",
            "Valor Líquido": 2300.00,
            "Valor Nominal": 2300.00,
            "Juros": 0.00,
            "Desconto": 0.00,
            "Contrato": "",
        },
        {
            # Desmembramento SAC Bradesco #4410: Passivo baixou R$ 10.000,00 (correto), mas Juros pagos foram R$ 2.790,00 vs R$ 2.380,00 teóricos!
            "Código Lançamento": "OM-2003A",
            "Data da Baixa": "20/10/2026",
            "Data de Vencimento": "20/10/2026",
            "Situação": "Baixado",
            "Tipo": "Saída",
            "Banco": "Bradesco",
            "Conta Corrente": "99812-3",
            "Categoria": "2.03.01 - Passivo - Amortização de Empréstimos a Pagar",
            "Cliente / Fornecedor": "Banco Bradesco S.A.",
            "Número do Documento": "CTR4410-P02",
            "Observações": "Amortização principal parcela 02/18 Contrato 4410",
            "Valor Líquido": -10000.00,
            "Valor Nominal": 10000.00,
            "Juros": 0.00,
            "Desconto": 0.00,
            "Contrato": "CTR-SAC-237",
        },
        {
            "Código Lançamento": "OM-2003B",
            "Data da Baixa": "20/10/2026",
            "Data de Vencimento": "20/10/2026",
            "Situação": "Baixado",
            "Tipo": "Saída",
            "Banco": "Bradesco",
            "Conta Corrente": "99812-3",
            "Categoria": "2.04.02 - Despesa Financeira - Juros de Financiamento",
            "Cliente / Fornecedor": "Banco Bradesco S.A.",
            "Número do Documento": "CTR4410-P02",
            "Observações": "Juros parcela 02/18 Contrato 4410 cobrados acima da tabela SAC",
            "Valor Líquido": -2790.00,
            "Valor Nominal": 2790.00,
            "Juros": 2790.00,
            "Desconto": 0.00,
            "Contrato": "CTR-SAC-237",
        },
    ]
    omie_df = pd.DataFrame(omie_rows)
    omie_xlsx_path = target_dir / "omie_movimentacao_out2026.xlsx"
    omie_csv_path = target_dir / "omie_movimentacao_out2026.csv"
    omie_df.to_excel(omie_xlsx_path, index=False)
    omie_df.to_csv(omie_csv_path, index=False, sep=";", decimal=",", encoding="utf-8-sig")

    return {
        "ofx_itau": ofx_path,
        "csv_bradesco": csv_bank_path,
        "omie_xlsx": omie_xlsx_path,
        "omie_csv": omie_csv_path,
    }
