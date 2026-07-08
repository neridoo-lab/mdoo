import pywhatkit
import time
from datetime import datetime, timedelta
import sqlite3
from database import get_db

class WhatsAppService:
    def __init__(self):
        self.tipo_api = self.get_tipo_api()
    
    def get_tipo_api(self):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT valor FROM configuracoes WHERE chave = "whatsapp_api_tipo"')
        result = cursor.fetchone()
        conn.close()
        return result['valor'] if result else 'web'
    
    def get_config_notificacao(self):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT chave, valor FROM configuracoes 
            WHERE chave IN ('notificacao_horario', 'notificacao_dias_antecedencia', 'mensagem_notificacao')
        ''')
        configs = {row['chave']: row['valor'] for row in cursor.fetchall()}
        conn.close()
        return configs
    
    def formatar_telefone(self, telefone):
        import re
        telefone = re.sub(r'\D', '', str(telefone))
        if len(telefone) <= 11:
            telefone = '+55' + telefone
        return telefone
    
    def formatar_moeda(self, valor):
        try:
            return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except:
            return "R$ 0,00"
    
    def formatar_data(self, data_str):
        try:
            data = datetime.strptime(str(data_str), '%Y-%m-%d')
            return data.strftime('%d/%m/%Y')
        except:
            return data_str
    
    def preparar_mensagem(self, template, dados):
        mensagem = template
        for chave, valor in dados.items():
            mensagem = mensagem.replace(f'{{{chave}}}', str(valor))
        return mensagem
    
    def enviar_mensagem_web(self, telefone, mensagem):
        try:
            telefone_formatado = self.formatar_telefone(telefone)
            agora = datetime.now()
            hora = agora.hour
            minuto = agora.minute + 2
            
            if minuto >= 60:
                hora += 1
                minuto -= 60
            
            pywhatkit.sendwhatmsg(telefone_formatado, mensagem, hora, minuto, 15, True, 5)
            return True, "Mensagem enviada com sucesso"
        except Exception as e:
            return False, f"Erro ao enviar mensagem: {str(e)}"
    
    def registrar_notificacao(self, parcela_id, cliente_id, telefone, mensagem, status):
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO historico_notificacoes (parcela_id, cliente_id, telefone, mensagem, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (parcela_id, cliente_id, telefone, mensagem, status))
        
        if status == 'enviada':
            cursor.execute('''
                UPDATE parcelas 
                SET notificacao_enviada = 1, data_notificacao = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (parcela_id,))
        
        conn.commit()
        conn.close()
    
    def verificar_parcelas_para_notificar(self):
        conn = get_db()
        cursor = conn.cursor()
        
        configs = self.get_config_notificacao()
        dias_antecedencia = int(configs.get('notificacao_dias_antecedencia', 3))
        
        data_limite = (datetime.now() + timedelta(days=dias_antecedencia)).strftime('%Y-%m-%d')
        data_hoje = datetime.now().strftime('%Y-%m-%d')
        
        cursor.execute('''
            SELECT p.*, c.nome as cliente_nome, c.telefone, c.id as cliente_id,
                   c.notificacoes_ativas, c.dias_antecedencia_notificacao,
                   e.valor as valor_emprestimo, e.juros_vencimento,
                   e.quantidade_parcelas, e.id as emprestimo_id
            FROM parcelas p
            JOIN emprestimos e ON p.emprestimo_id = e.id
            JOIN clientes c ON e.cliente_id = c.id
            WHERE p.status = 'pendente'
            AND p.data_vencimento BETWEEN ? AND ?
            AND p.notificacao_enviada = 0
            AND c.notificacoes_ativas = 1
            AND e.notificacao_ativa = 1
            AND c.telefone IS NOT NULL
            AND c.telefone != ''
            ORDER BY p.data_vencimento
        ''', (data_hoje, data_limite))
        
        parcelas = cursor.fetchall()
        conn.close()
        
        return parcelas
    
    def enviar_notificacoes_automaticas(self):
        parcelas = self.verificar_parcelas_para_notificar()
        
        if not parcelas:
            return {"status": "info", "message": "Nenhuma parcela para notificar"}
        
        configs = self.get_config_notificacao()
        template_mensagem = configs.get('mensagem_notificacao', '')
        
        resultados = []
        
        for parcela in parcelas:
            dados_mensagem = {
                'nome_cliente': parcela['cliente_nome'],
                'numero_parcela': parcela['numero'],
                'total_parcelas': parcela['quantidade_parcelas'],
                'valor_parcela': self.formatar_moeda(parcela['valor']),
                'data_vencimento': self.formatar_data(parcela['data_vencimento']),
                'valor_emprestimo': self.formatar_moeda(parcela['valor_emprestimo']),
                'juros_vencimento': parcela['juros_vencimento'],
                'dias_para_vencimento': (datetime.strptime(parcela['data_vencimento'], '%Y-%m-%d') - datetime.now()).days
            }
            
            mensagem = self.preparar_mensagem(template_mensagem, dados_mensagem)
            sucesso, msg_status = self.enviar_mensagem_web(parcela['telefone'], mensagem)
            
            status = 'enviada' if sucesso else 'erro'
            self.registrar_notificacao(
                parcela['id'], 
                parcela['cliente_id'], 
                parcela['telefone'], 
                mensagem, 
                status
            )
            
            resultados.append({
                'cliente': parcela['cliente_nome'],
                'parcela': parcela['numero'],
                'sucesso': sucesso,
                'mensagem': msg_status
            })
            
            time.sleep(5)
        
        return {
            "status": "success",
            "message": f"{len(resultados)} notificações processadas",
            "resultados": resultados
        }
    
    def enviar_notificacao_individual(self, parcela_id):
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT p.*, c.nome as cliente_nome, c.telefone, c.id as cliente_id,
                   c.notificacoes_ativas, c.dias_antecedencia_notificacao,
                   e.valor as valor_emprestimo, e.juros_vencimento,
                   e.quantidade_parcelas
            FROM parcelas p
            JOIN emprestimos e ON p.emprestimo_id = e.id
            JOIN clientes c ON e.cliente_id = c.id
            WHERE p.id = ?
        ''', (parcela_id,))
        
        parcela = cursor.fetchone()
        
        if not parcela:
            conn.close()
            return {"status": "error", "message": "Parcela não encontrada"}
        
        if not parcela['telefone']:
            conn.close()
            return {"status": "error", "message": "Cliente não possui telefone cadastrado"}
        
        configs = self.get_config_notificacao()
        template_mensagem = configs.get('mensagem_notificacao', '')
        
        dados_mensagem = {
            'nome_cliente': parcela['cliente_nome'],
            'numero_parcela': parcela['numero'],
            'total_parcelas': parcela['quantidade_parcelas'],
            'valor_parcela': self.formatar_moeda(parcela['valor']),
            'data_vencimento': self.formatar_data(parcela['data_vencimento']),
            'valor_emprestimo': self.formatar_moeda(parcela['valor_emprestimo']),
            'juros_vencimento': parcela['juros_vencimento'],
            'dias_para_vencimento': (datetime.strptime(parcela['data_vencimento'], '%Y-%m-%d') - datetime.now()).days
        }
        
        mensagem = self.preparar_mensagem(template_mensagem, dados_mensagem)
        sucesso, msg_status = self.enviar_mensagem_web(parcela['telefone'], mensagem)
        
        status = 'enviada' if sucesso else 'erro'
        self.registrar_notificacao(
            parcela['id'], 
            parcela['cliente_id'], 
            parcela['telefone'], 
            mensagem, 
            status
        )
        
        conn.close()
        
        return {
            "status": "success" if sucesso else "error",
            "message": msg_status
        }