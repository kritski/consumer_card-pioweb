from flask import Flask, request, jsonify
import os
import requests
import json
import uuid
import datetime

app = Flask(__name__)

# Configuração
MAKE_WEBHOOK_URL = os.environ.get('MAKE_WEBHOOK_URL', 'https://hook.eu2.make.com/YOUR_WEBHOOK_ID')
API_TOKEN = os.environ.get('API_TOKEN', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkNhcmRhcGlvV2ViIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c')

# Armazenamento temporário de pedidos (em produção, use um banco de dados)
orders_cache = {}

# Middleware para verificar o token de autenticação
@app.before_request
def verify_token():
    # Ignorar verificação para rotas de saúde/diagnóstico
    if request.path == '/health':
        return
    
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Token de autenticação ausente ou inválido'}), 401
    
    token = auth_header.split(' ')[1]
    if token != API_TOKEN:
        return jsonify({'error': 'Token de autenticação inválido'}), 401

# Rota de saúde para verificar se a API está funcionando
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok', 'timestamp': datetime.datetime.now().isoformat()})

# Endpoint de polling - Consumer consulta novos pedidos
@app.route('/api/parceiro/polling', methods=['GET'])
def polling():
    try:
        # Chamar o webhook do Make.com para buscar pedidos
        response = requests.post(
            MAKE_WEBHOOK_URL,
            json={
                'action': 'polling',
                'timestamp': datetime.datetime.now().isoformat()
            },
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code != 200:
            return jsonify({'error': 'Erro ao buscar pedidos no Make.com'}), 500
        
        # Processar a resposta do Make.com
        make_data = response.json()
        
        # Formatar a resposta no padrão esperado pelo Consumer
        items = []
        for order in make_data.get('orders', []):
            order_id = order.get('id')
            # Armazenar no cache para uso posterior
            orders_cache[str(order_id)] = order
            
            items.append({
                'id': str(uuid.uuid4()),  # ID único do evento
                'orderId': str(order_id),
                'createdAt': order.get('created_at'),
                'fullCode': 'PLACED',
                'code': 'PLC'
            })
        
        consumer_response = {
            'items': items,
            'statusCode': 0,
            'reasonPhrase': None
        }
        
        return jsonify(consumer_response)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Endpoint de detalhes do pedido - Consumer consulta detalhes de um pedido específico
@app.route('/api/parceiro/order/<order_id>', methods=['GET'])
def order_details(order_id):
    try:
        # Chamar o webhook do Make.com para buscar detalhes do pedido
        response = requests.post(
            MAKE_WEBHOOK_URL,
            json={
                'action': 'order_details',
                'order_id': order_id,
                'timestamp': datetime.datetime.now().isoformat()
            },
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code != 200:
            return jsonify({'error': 'Erro ao buscar detalhes do pedido no Make.com'}), 500
        
        # Processar a resposta do Make.com
        make_data = response.json()
        
        # Retornar os detalhes do pedido no formato esperado pelo Consumer
        return jsonify(make_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Endpoint de mudança de status - Consumer atualiza o status de um pedido
@app.route('/api/parceiro/order/<order_id>', methods=['POST'])
def update_order_status(order_id):
    try:
        # Obter os dados da requisição
        status_data = request.json
        
        # Chamar o webhook do Make.com para atualizar o status do pedido
        response = requests.post(
            MAKE_WEBHOOK_URL,
            json={
                'action': 'update_status',
                'order_id': order_id,
                'status': status_data,
                'timestamp': datetime.datetime.now().isoformat()
            },
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code != 200:
            return jsonify({'error': 'Erro ao atualizar status do pedido no Make.com'}), 500
        
        # Processar a resposta do Make.com
        make_data = response.json()
        
        # Retornar a confirmação no formato esperado pelo Consumer
        return jsonify({
            'success': True,
            'message': 'Status do pedido atualizado com sucesso',
            'data': make_data
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
