import sqlite3
from datetime import datetime

def init_db():
    conn = sqlite3.connect('emprestimos.db', timeout=10)
    conn.execute('PRAGMA journal_mode=WAL')
    cursor = conn.cursor()
    
    # Tabela de clientes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT,
            email TEXT,
            data_cadastro DATE DEFAULT CURRENT_DATE,
            notificacoes_ativas INTEGER DEFAULT 1,
            dias_antecedencia_notificacao INTEGER DEFAULT 3
        )
    ''')
    
    # Tabela de empréstimos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS emprestimos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            valor REAL NOT NULL,
            juros_mensal REAL NOT NULL,
            juros_vencimento REAL DEFAULT 0,
            quantidade_parcelas INTEGER NOT NULL,
            data_primeira_parcela DATE NOT NULL,
            data_emprestimo DATE DEFAULT CURRENT_DATE,
            notificacao_ativa INTEGER DEFAULT 1,
            FOREIGN KEY (cliente_id) REFERENCES clientes (id)
        )
    ''')
    
    # Tabela de parcelas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parcelas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emprestimo_id INTEGER NOT NULL,
            numero INTEGER NOT NULL,
            valor_original REAL NOT NULL,
            valor REAL NOT NULL,
            data_vencimento DATE NOT NULL,
            data_pagamento DATE,
            status TEXT DEFAULT 'pendente',
            dias_atraso INTEGER DEFAULT 0,
            juros_vencimento_aplicado REAL DEFAULT 0,
            notificacao_enviada INTEGER DEFAULT 0,
            data_notificacao DATETIME,
            FOREIGN KEY (emprestimo_id) REFERENCES emprestimos (id)
        )
    ''')
    
    # Tabela de configurações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS configuracoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chave TEXT UNIQUE NOT NULL,
            valor TEXT NOT NULL
        )
    ''')
    
    # Inserir configurações padrão
    configuracoes_padrao = [
        ('juros_vencimento_padrao', '10'),
        ('notificacao_horario', '09:00'),
        ('notificacao_dias_antecedencia', '3'),
        ('whatsapp_api_tipo', 'web'),
        ('mensagem_notificacao', '''Olá {nome_cliente}! 

Lembrete: Sua parcela {numero_parcela}/{total_parcelas} no valor de {valor_parcela} vence em {data_vencimento}.

Valor total do empréstimo: {valor_emprestimo}
Juros por atraso: {juros_vencimento}% ao mês

Para pagamento, entre em contato!
Atenciosamente,
{Nome da Empresa}''')
    ]
    
    for chave, valor in configuracoes_padrao:
        cursor.execute('''
            INSERT OR IGNORE INTO configuracoes (chave, valor)
            VALUES (?, ?)
        ''', (chave, valor))
    
    # Tabela de histórico de juros de vencimento
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico_juros_vencimento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parcela_id INTEGER NOT NULL,
            data_aplicacao DATETIME DEFAULT CURRENT_TIMESTAMP,
            valor_original REAL NOT NULL,
            percentual_juros REAL NOT NULL,
            valor_juros REAL NOT NULL,
            novo_valor REAL NOT NULL,
            FOREIGN KEY (parcela_id) REFERENCES parcelas (id)
        )
    ''')
    
    # Tabela de histórico de notificações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico_notificacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parcela_id INTEGER NOT NULL,
            cliente_id INTEGER NOT NULL,
            telefone TEXT NOT NULL,
            mensagem TEXT NOT NULL,
            status TEXT NOT NULL,
            data_envio DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parcela_id) REFERENCES parcelas (id),
            FOREIGN KEY (cliente_id) REFERENCES clientes (id)
        )
    ''')
    
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect('emprestimos.db', timeout=10)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.row_factory = sqlite3.Row
    return conn