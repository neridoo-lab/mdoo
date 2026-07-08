from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from database import init_db, get_db
from whatsapp_service import WhatsAppService
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui_123'

@app.context_processor
def inject_now():
    return {'now': datetime.now()}

init_db()
whatsapp_service = WhatsAppService()

scheduler = BackgroundScheduler()
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

def formatar_moeda(valor):
    try:
        return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "R$ 0,00"

def verificar_e_aplicar_juros_vencimento():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.*, e.juros_vencimento 
        FROM parcelas p
        JOIN emprestimos e ON p.emprestimo_id = e.id
        WHERE p.status = 'pendente' 
        AND p.data_vencimento < DATE('now')
    ''')
    parcelas_vencidas = cursor.fetchall()
    for parcela in parcelas_vencidas:
        data_vencimento = datetime.strptime(parcela['data_vencimento'], '%Y-%m-%d')
        dias_atraso = (datetime.now() - data_vencimento).days
        if dias_atraso == parcela['dias_atraso']:
            continue
        if parcela['juros_vencimento'] > 0 and dias_atraso > 0:
            juros_diario = parcela['juros_vencimento'] / 30
            valor_atual = parcela['valor']
            for dia in range(parcela['dias_atraso'], dias_atraso):
                valor_juros = valor_atual * (juros_diario / 100)
                novo_valor = valor_atual + valor_juros
                cursor.execute('''
                    INSERT INTO historico_juros_vencimento 
                    (parcela_id, valor_original, percentual_juros, valor_juros, novo_valor)
                    VALUES (?, ?, ?, ?, ?)
                ''', (parcela['id'], valor_atual, juros_diario, valor_juros, novo_valor))
                cursor.execute('''
                    UPDATE parcelas 
                    SET valor = ?, dias_atraso = ?, juros_vencimento_aplicado = juros_vencimento_aplicado + ?
                    WHERE id = ?
                ''', (round(novo_valor, 2), dia + 1, valor_juros, parcela['id']))
                valor_atual = novo_valor
            cursor.execute('''
                UPDATE parcelas 
                SET dias_atraso = ?
                WHERE id = ?
            ''', (dias_atraso, parcela['id']))
    conn.commit()
    conn.close()

scheduler.add_job(func=verificar_e_aplicar_juros_vencimento, trigger="interval", hours=1)
scheduler.add_job(func=whatsapp_service.enviar_notificacoes_automaticas, trigger="interval", hours=1)

@app.route('/')
def index():
    verificar_e_aplicar_juros_vencimento()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(valor) as total FROM emprestimos')
    total_investido = cursor.fetchone()['total'] or 0
    cursor.execute('SELECT COALESCE(SUM(valor), 0) as total FROM parcelas WHERE status = "pago"')
    total_recebido = cursor.fetchone()['total'] or 0
    cursor.execute('SELECT COALESCE(SUM(valor), 0) as total FROM parcelas WHERE status = "pendente"')
    total_a_receber = cursor.fetchone()['total'] or 0
    cursor.execute('SELECT COALESCE(SUM(juros_vencimento_aplicado), 0) as total FROM parcelas WHERE status = "pendente"')
    total_juros_vencimento = cursor.fetchone()['total'] or 0
    cursor.execute('SELECT COUNT(*) as total FROM clientes')
    total_clientes = cursor.fetchone()['total'] or 0
    data_limite = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT COUNT(DISTINCT e.cliente_id) as total
        FROM parcelas p
        JOIN emprestimos e ON p.emprestimo_id = e.id
        WHERE p.status = 'pendente' 
        AND p.data_vencimento <= ?
        AND p.data_vencimento >= DATE('now')
    ''', (data_limite,))
    proximos_vencimento = cursor.fetchone()['total'] or 0
    cursor.execute('''
        SELECT COUNT(DISTINCT e.cliente_id) as total
        FROM parcelas p
        JOIN emprestimos e ON p.emprestimo_id = e.id
        WHERE p.status = 'pendente' 
        AND p.data_vencimento < DATE('now')
    ''')
    vencidos = cursor.fetchone()['total'] or 0
    conn.close()
    return render_template('index.html',
                         total_investido=total_investido,
                         total_recebido=total_recebido,
                         total_a_receber=total_a_receber,
                         total_juros_vencimento=total_juros_vencimento,
                         total_clientes=total_clientes,
                         proximos_vencimento=proximos_vencimento,
                         vencidos=vencidos,
                         formatar_moeda=formatar_moeda)

@app.route('/clientes')
def clientes():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.*, 
               COUNT(e.id) as total_emprestimos,
               COALESCE(SUM(e.valor), 0) as total_emprestado,
               COALESCE(SUM(e.valor * (1 + e.juros_mensal/100 * e.quantidade_parcelas)), 0) as total_a_receber
        FROM clientes c
        LEFT JOIN emprestimos e ON c.id = e.cliente_id
        GROUP BY c.id
        ORDER BY c.nome
    ''')
    clientes = cursor.fetchall()
    conn.close()
    return render_template('clientes.html', clientes=clientes, formatar_moeda=formatar_moeda)

@app.route('/cliente/<int:cliente_id>')
def cliente_detalhes(cliente_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM clientes WHERE id = ?', (cliente_id,))
    cliente = cursor.fetchone()
    if not cliente:
        flash('Cliente não encontrado', 'danger')
        return redirect(url_for('clientes'))
    cursor.execute('''
        SELECT e.*,
               (SELECT COUNT(*) FROM parcelas WHERE emprestimo_id = e.id AND status = 'pago') as parcelas_pagas,
               (SELECT COUNT(*) FROM parcelas WHERE emprestimo_id = e.id AND status = 'pendente') as parcelas_pendentes,
               (SELECT COALESCE(SUM(valor), 0) FROM parcelas WHERE emprestimo_id = e.id AND status = 'pago') as total_pago,
               (SELECT COALESCE(SUM(valor), 0) FROM parcelas WHERE emprestimo_id = e.id AND status = 'pendente') as total_pendente
        FROM emprestimos e
        WHERE e.cliente_id = ?
        ORDER BY e.id DESC
    ''', (cliente_id,))
    emprestimos = cursor.fetchall()
    emprestimos_dict = []
    for emp in emprestimos:
        emp_dict = dict(emp)
        cursor.execute('''
            SELECT * FROM parcelas 
            WHERE emprestimo_id = ? 
            ORDER BY numero
        ''', (emp['id'],))
        emp_dict['parcelas'] = cursor.fetchall()
        emprestimos_dict.append(emp_dict)
    conn.close()
    return render_template('cliente_detalhes.html', 
                         cliente=cliente, 
                         emprestimos=emprestimos_dict,
                         formatar_moeda=formatar_moeda)

@app.route('/faturas')
def faturas():
    cliente_id = request.args.get('cliente_id', type=int)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, nome FROM clientes ORDER BY nome')
    clientes = cursor.fetchall()
    if cliente_id:
        cursor.execute('''
            SELECT p.*, c.nome as cliente_nome, e.valor as valor_emprestimo,
                   e.juros_mensal, e.juros_vencimento,
                   CASE WHEN p.data_vencimento < DATE('now') 
                        THEN CAST(julianday('now') - julianday(p.data_vencimento) AS INTEGER)
                        ELSE 0 
                   END as dias_atraso
            FROM parcelas p
            JOIN emprestimos e ON p.emprestimo_id = e.id
            JOIN clientes c ON e.cliente_id = c.id
            WHERE c.id = ?
            ORDER BY p.data_vencimento DESC, p.id DESC
        ''', (cliente_id,))
    else:
        cursor.execute('''
            SELECT p.*, c.nome as cliente_nome, e.valor as valor_emprestimo,
                   e.juros_mensal, e.juros_vencimento,
                   CASE WHEN p.data_vencimento < DATE('now') 
                        THEN CAST(julianday('now') - julianday(p.data_vencimento) AS INTEGER)
                        ELSE 0 
                   END as dias_atraso
            FROM parcelas p
            JOIN emprestimos e ON p.emprestimo_id = e.id
            JOIN clientes c ON e.cliente_id = c.id
            ORDER BY p.data_vencimento DESC, p.id DESC
        ''')
    parcelas = cursor.fetchall()
    conn.close()
    return render_template('faturas.html', 
                         parcelas=parcelas, 
                         clientes=clientes,
                         cliente_id=cliente_id,
                         formatar_moeda=formatar_moeda)

@app.route('/cadastrar_cliente', methods=['GET', 'POST'])
def cadastrar_cliente():
    if request.method == 'POST':
        nome = request.form['nome']
        telefone = request.form.get('telefone', '')
        email = request.form.get('email', '')
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO clientes (nome, telefone, email) VALUES (?, ?, ?)',
                      (nome, telefone, email))
        conn.commit()
        conn.close()
        flash('Cliente cadastrado com sucesso!', 'success')
        return redirect(url_for('clientes'))
    return render_template('cadastrar_cliente.html')

@app.route('/adicionar_emprestimo', methods=['GET', 'POST'])
def adicionar_emprestimo():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT valor FROM configuracoes WHERE chave = "juros_vencimento_padrao"')
    config = cursor.fetchone()
    juros_vencimento_padrao = float(config['valor']) if config else 10.0
    if request.method == 'POST':
        cliente_id = request.form['cliente_id']
        valor = float(request.form['valor'])
        juros_mensal = float(request.form['juros_mensal'])
        juros_vencimento = float(request.form.get('juros_vencimento', juros_vencimento_padrao))
        quantidade_parcelas = int(request.form['quantidade_parcelas'])
        data_primeira_parcela = request.form['data_primeira_parcela']
        cursor.execute('''
            INSERT INTO emprestimos (cliente_id, valor, juros_mensal, juros_vencimento, 
                                   quantidade_parcelas, data_primeira_parcela)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (cliente_id, valor, juros_mensal, juros_vencimento, 
              quantidade_parcelas, data_primeira_parcela))
        emprestimo_id = cursor.lastrowid
        valor_total = valor * (1 + juros_mensal/100 * quantidade_parcelas)
        valor_parcela = valor_total / quantidade_parcelas
        data_parcela = datetime.strptime(data_primeira_parcela, '%Y-%m-%d')
        for i in range(1, quantidade_parcelas + 1):
            cursor.execute('''
                INSERT INTO parcelas (emprestimo_id, numero, valor_original, valor, 
                                    data_vencimento, status)
                VALUES (?, ?, ?, ?, ?, 'pendente')
            ''', (emprestimo_id, i, round(valor_parcela, 2), round(valor_parcela, 2), 
                  data_parcela.strftime('%Y-%m-%d')))
            if data_parcela.month == 12:
                data_parcela = data_parcela.replace(year=data_parcela.year + 1, month=1)
            else:
                data_parcela = data_parcela.replace(month=data_parcela.month + 1)
        conn.commit()
        conn.close()
        flash('Empréstimo adicionado com sucesso!', 'success')
        return redirect(url_for('index'))
    cursor.execute('SELECT id, nome FROM clientes ORDER BY nome')
    clientes = cursor.fetchall()
    conn.close()
    return render_template('adicionar_emprestimo.html', 
                         clientes=clientes, 
                         juros_vencimento_padrao=juros_vencimento_padrao)

@app.route('/faturar_parcela', methods=['GET', 'POST'])
def faturar_parcela():
    conn = get_db()
    cursor = conn.cursor()
    if request.method == 'POST':
        parcela_id = request.form['parcela_id']
        valor_pago = float(request.form.get('valor_pago', 0))
        cursor.execute('SELECT * FROM parcelas WHERE id = ?', (parcela_id,))
        parcela = cursor.fetchone()
        if parcela:
            if valor_pago > 0:
                cursor.execute('''
                    UPDATE parcelas 
                    SET status = 'pago', data_pagamento = DATE('now'), valor = ?
                    WHERE id = ?
                ''', (valor_pago, parcela_id))
            else:
                cursor.execute('''
                    UPDATE parcelas 
                    SET status = 'pago', data_pagamento = DATE('now')
                    WHERE id = ?
                ''', (parcela_id,))
        conn.commit()
        conn.close()
        flash('Parcela faturada com sucesso!', 'success')
        return redirect(url_for('index'))
    cursor.execute('''
        SELECT p.*, c.nome as cliente_nome, e.valor as valor_emprestimo,
               e.juros_mensal, e.juros_vencimento,
               CASE WHEN p.data_vencimento < DATE('now') 
                    THEN CAST(julianday('now') - julianday(p.data_vencimento) AS INTEGER)
                    ELSE 0 
               END as dias_atraso
        FROM parcelas p
        JOIN emprestimos e ON p.emprestimo_id = e.id
        JOIN clientes c ON e.cliente_id = c.id
        WHERE p.status = 'pendente'
        ORDER BY p.data_vencimento
    ''')
    parcelas = cursor.fetchall()
    conn.close()
    return render_template('faturar_parcela.html', 
                         parcelas=parcelas, 
                         formatar_moeda=formatar_moeda)

@app.route('/configurar_juros_vencimento', methods=['GET', 'POST'])
def configurar_juros_vencimento():
    conn = get_db()
    cursor = conn.cursor()
    if request.method == 'POST':
        juros_padrao = float(request.form['juros_padrao'])
        cursor.execute('''
            UPDATE configuracoes SET valor = ? WHERE chave = 'juros_vencimento_padrao'
        ''', (str(juros_padrao),))
        conn.commit()
        conn.close()
        flash(f'Juros de vencimento padrão atualizado para {juros_padrao}% ao mês!', 'success')
        return redirect(url_for('configurar_juros_vencimento'))
    cursor.execute('SELECT valor FROM configuracoes WHERE chave = "juros_vencimento_padrao"')
    config = cursor.fetchone()
    juros_atual = float(config['valor']) if config else 10.0
    conn.close()
    return render_template('configurar_juros.html', juros_atual=juros_atual)

@app.route('/configurar_notificacoes', methods=['GET', 'POST'])
def configurar_notificacoes():
    conn = get_db()
    cursor = conn.cursor()
    if request.method == 'POST':
        configs = {
            'notificacao_horario': request.form['horario'],
            'notificacao_dias_antecedencia': request.form['dias_antecedencia'],
            'whatsapp_api_tipo': request.form['api_tipo'],
            'mensagem_notificacao': request.form['mensagem']
        }
        for chave, valor in configs.items():
            cursor.execute('UPDATE configuracoes SET valor = ? WHERE chave = ?', 
                         (valor, chave))
        conn.commit()
        conn.close()
        flash('Configurações atualizadas com sucesso!', 'success')
        return redirect(url_for('configurar_notificacoes'))
    cursor.execute('''
        SELECT chave, valor FROM configuracoes 
        WHERE chave IN ('notificacao_horario', 'notificacao_dias_antecedencia', 
                       'whatsapp_api_tipo', 'mensagem_notificacao')
    ''')
    configs = {row['chave']: row['valor'] for row in cursor.fetchall()}
    conn.close()
    return render_template('configurar_notificacoes.html', configs=configs)

@app.route('/enviar_notificacoes', methods=['POST'])
def enviar_notificacoes():
    resultado = whatsapp_service.enviar_notificacoes_automaticas()
    if resultado['status'] == 'success':
        flash(f'Notificações enviadas: {resultado["message"]}', 'success')
    else:
        flash(resultado['message'], 'info')
    return redirect(url_for('index'))

@app.route('/enviar_notificacao_individual/<int:parcela_id>', methods=['POST'])
def enviar_notificacao_individual(parcela_id):
    resultado = whatsapp_service.enviar_notificacao_individual(parcela_id)
    if resultado['status'] == 'success':
        flash('Notificação enviada com sucesso!', 'success')
    else:
        flash(f'Erro: {resultado["message"]}', 'danger')
    return redirect(request.referrer or url_for('index'))

@app.route('/historico_notificacoes')
def historico_notificacoes():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT h.*, c.nome as cliente_nome, p.numero as numero_parcela
        FROM historico_notificacoes h
        JOIN clientes c ON h.cliente_id = c.id
        JOIN parcelas p ON h.parcela_id = p.id
        ORDER BY h.data_envio DESC
        LIMIT 100
    ''')
    notificacoes = cursor.fetchall()
    conn.close()
    return render_template('historico_notificacoes.html', notificacoes=notificacoes)

@app.route('/historico_juros/<int:parcela_id>')
def historico_juros(parcela_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT h.*, p.numero, p.valor_original, c.nome as cliente_nome
        FROM historico_juros_vencimento h
        JOIN parcelas p ON h.parcela_id = p.id
        JOIN emprestimos e ON p.emprestimo_id = e.id
        JOIN clientes c ON e.cliente_id = c.id
        WHERE h.parcela_id = ?
        ORDER BY h.data_aplicacao DESC
    ''', (parcela_id,))
    historico = cursor.fetchall()
    cursor.execute('''
        SELECT p.*, c.nome as cliente_nome
        FROM parcelas p
        JOIN emprestimos e ON p.emprestimo_id = e.id
        JOIN clientes c ON e.cliente_id = c.id
        WHERE p.id = ?
    ''', (parcela_id,))
    parcela = cursor.fetchone()
    conn.close()
    return render_template('historico_juros.html', 
                         historico=historico, 
                         parcela=parcela,
                         formatar_moeda=formatar_moeda)

@app.route('/atualizar_preferencias_cliente/<int:cliente_id>', methods=['POST'])
def atualizar_preferencias_cliente(cliente_id):
    notificacoes_ativas = request.form.get('notificacoes_ativas') == 'on'
    dias_antecedencia = request.form.get('dias_antecedencia', 3)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE clientes 
        SET notificacoes_ativas = ?, dias_antecedencia_notificacao = ?
        WHERE id = ?
    ''', (notificacoes_ativas, dias_antecedencia, cliente_id))
    conn.commit()
    conn.close()
    flash('Preferências atualizadas!', 'success')
    return redirect(url_for('clientes'))

@app.route('/proximos_vencimento')
def proximos_vencimento():
    conn = get_db()
    cursor = conn.cursor()
    data_limite = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')
    cursor.execute('''
        SELECT DISTINCT c.*, 
               p.id as parcela_id,
               p.data_vencimento,
               p.valor_original,
               p.valor as valor_atual,
               p.juros_vencimento_aplicado,
               p.numero,
               e.valor as valor_emprestimo,
               e.quantidade_parcelas,
               e.juros_vencimento
        FROM parcelas p
        JOIN emprestimos e ON p.emprestimo_id = e.id
        JOIN clientes c ON e.cliente_id = c.id
        WHERE p.status = 'pendente' 
        AND p.data_vencimento <= ?
        AND p.data_vencimento >= DATE('now')
        ORDER BY p.data_vencimento
    ''', (data_limite,))
    clientes = cursor.fetchall()
    conn.close()
    return render_template('proximos_vencimento.html', 
                         clientes=clientes, 
                         formatar_moeda=formatar_moeda)

@app.route('/vencidos')
def vencidos():
    verificar_e_aplicar_juros_vencimento()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT c.*, 
               p.id as parcela_id,
               p.data_vencimento,
               p.valor_original,
               p.valor as valor_atual,
               p.juros_vencimento_aplicado,
               p.numero,
               e.valor as valor_emprestimo,
               e.quantidade_parcelas,
               e.juros_vencimento,
               CAST(julianday('now') - julianday(p.data_vencimento) AS INTEGER) as dias_atraso
        FROM parcelas p
        JOIN emprestimos e ON p.emprestimo_id = e.id
        JOIN clientes c ON e.cliente_id = c.id
        WHERE p.status = 'pendente' 
        AND p.data_vencimento < DATE('now')
        ORDER BY p.data_vencimento
    ''')
    clientes = cursor.fetchall()
    conn.close()
    return render_template('vencidos.html', 
                         clientes=clientes, 
                         formatar_moeda=formatar_moeda)

if __name__ == '__main__':
    app.run(debug=True, port=5000)