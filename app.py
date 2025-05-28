from dotenv import load_dotenv
load_dotenv()  # Carrega variáveis do arquivo .env

from flask import Flask, request, jsonify, Response
from typing import Dict, Any, Optional, Tuple, List
import os
import requests
import json
import uuid
import datetime
from datetime import datetime, timezone, timedelta
import logging
from logging.handlers import RotatingFileHandler

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('app.log', maxBytes=10000, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuração
MAKE_WEBHOOK_URL = os.environ.get('MAKE_WEBHOOK_URL', 'https://hook.eu2.make.com/YOUR_WEBHOOK_ID')
API_TOKEN = os.environ.get('API_TOKEN', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkNhcmRhcGlvV2ViIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c')

# Armazenamento temporário de pedidos (em produção, use um banco de dados)
orders_cache = {}

# Função para validar o token de autenticação
def verify_token_auth(auth_header):
    """
    Verifica o token de autenticação
    """
    if not auth_header:
        return False, "Token não fornecido"
    
    try:
        # Formato esperado: "Bearer TOKEN"
        if not auth_header.startswith('Bearer '):
            return False, "Formato de autenticação inválido"
        
        token = auth_header.split(' ')[1]
        if token != API_TOKEN:
            return False, "Token inválido"
        
        return True, "Token válido"
    except ValueError:
        return False, "Formato de autenticação inválido"

# Middleware para verificar o token de autenticação
@app.before_request
def verify_token():
    # Ignorar verificação para rotas de saúde/diagnóstico
    if request.path == '/health':
        return
    
    auth_header = request.headers.get('Authorization')
    is_valid, message = verify_token_auth(auth_header)
    
    if not is_valid:
        return jsonify({'error': message}), 401

# Função para validar os dados do pedido
def validate_order_data(data):
    """
    Valida os dados do pedido recebidos
    """
    required_fields = ['id', 'created_at', 'order_type']
    for field in required_fields:
        if field not in data:
            return False, f"Campo obrigatório ausente: {field}"
    
    return True, "Dados válidos"

# Função para transformar os dados do CardápioWeb para o formato do Consumer
def transform_to_consumer_format(cardapio_order):
    """
    Transforma o formato do CardápioWeb para o formato do Consumer
    """
    # Extrair dados do cliente
    customer = cardapio_order.get('customer', {})
    customer_phone = customer.get('phone', '')
    
    # Extrair dados de pagamento
    payment_info = cardapio_order.get('payment', {})
    payment_method = payment_info.get('method', 'ONLINE')
    payment_type = 'CREDIT'  # Definir com base na lógica de negócio
    
    # Calcular valores
    subtotal = float(cardapio_order.get('subtotal', 0))
    delivery_fee = float(cardapio_order.get('delivery_fee', 0))
    total = float(cardapio_order.get('total', 0))
    
    # Criar objeto no formato do Consumer
    now = datetime.now(timezone.utc)
    expiration_time = now + timedelta(hours=24)
    
    return {
        "id": cardapio_order.get('id'),
        "displayId": cardapio_order.get('id'),
        "orderType": cardapio_order.get('order_type', 'DELIVERY'),
        "salesChannel": cardapio_order.get('sales_channel', 'MARKETPLACE'),
        "orderTiming": cardapio_order.get('order_timing', 'ASAP'),
        "createdAt": cardapio_order.get('created_at'),
        "preparationStartDateTime": cardapio_order.get('created_at'),
        "merchant": {
            "id": "14104",  # ID do estabelecimento no CardápioWeb
            "name": "Seu Restaurante"  # Nome do estabelecimento
        },
        "total": {
            "subTotal": subtotal,
            "deliveryFee": delivery_fee,
            "orderAmount": total,
            "benefits": 0,  # Se houver descontos
            "additionalFees": 0  # Se houver taxas adicionais
        },
        "payments": {
            "methods": [
                {
                    "method": payment_method,
                    "type": payment_type,
                    "currency": "BRL",
                    "value": total
                }
            ],
            "pending": 0,
            "prepaid": total
        },
        "customer": {
            "id": customer.get('id', ''),
            "name": customer.get('name', ''),
            "phone": {
                "number": customer_phone,
                "localizer": "123456",  # Gerar um código único ou usar um padrão
                "localizerExpiration": expiration_time.isoformat()
            }
        }
    }

# Rota de saúde para verificar se a API está funcionando
@app.route('/health', methods=['GET'])
def health_check():
    logger.info("Health check solicitado")
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

# Endpoint de polling - Consumer consulta novos pedidos
@app.route('/api/parceiro/polling', methods=['GET'])
def polling():
    logger.info("Polling solicitado pelo Consumer")
    try:
        # Chamar o webhook do Make.com para buscar pedidos
        response = requests.post(
            MAKE_WEBHOOK_URL,
            json={
                'action': 'polling',
                'timestamp': datetime.now().isoformat()
            },
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code != 200:
            logger.error(f"Erro ao buscar pedidos no Make.com: {response.status_code}")
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
        
        logger.info(f"Retornando {len(items)} pedidos para o Consumer")
        return jsonify(consumer_response)
    
    except Exception as e:
        logger.error(f"Erro no polling: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Endpoint de detalhes do pedido - Consumer consulta detalhes de um pedido específico
@app.route('/api/parceiro/order/<order_id>', methods=['GET'])
def order_details(order_id):
    logger.info(f"Detalhes do pedido solicitados para: {order_id}")
    try:
        # Chamar o webhook do Make.com para buscar detalhes do pedido
        response = requests.post(
            MAKE_WEBHOOK_URL,
            json={
                'action': 'order_details',
                'order_id': order_id,
                'timestamp': datetime.now().isoformat()
            },
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code != 200:
            logger.error(f"Erro ao buscar detalhes do pedido no Make.com: {response.status_code}")
            return jsonify({'error': 'Erro ao buscar detalhes do pedido no Make.com'}), 500
        
        # Processar a resposta do Make.com
        make_data = response.json()
        
        # Transformar para o formato do Consumer
        consumer_data = transform_to_consumer_format(make_data)
        
        # Retornar os detalhes do pedido no formato esperado pelo Consumer
        logger.info(f"Retornando detalhes do pedido: {order_id}")
        return jsonify(consumer_data)
    
    except Exception as e:
        logger.error(f"Erro ao obter detalhes do pedido: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Endpoint de mudança de status - Consumer atualiza o status de um pedido
@app.route('/api/parceiro/order/<order_id>', methods=['POST'])
def update_order_status(order_id):
    logger.info(f"Atualização de status solicitada para: {order_id}")
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
                'timestamp': datetime.now().isoformat()
            },
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code != 200:
            logger.error(f"Erro ao atualizar status do pedido no Make.com: {response.status_code}")
            return jsonify({'error': 'Erro ao atualizar status do pedido no Make.com'}), 500
        
        # Processar a resposta do Make.com
        make_data = response.json()
        
        # Retornar a confirmação no formato esperado pelo Consumer
        logger.info(f"Status do pedido atualizado com sucesso: {order_id}")
        return jsonify({
            'success': True,
            'message': 'Status do pedido atualizado com sucesso',
            'data': make_data
        })
    
    except Exception as e:
        logger.error(f"Erro ao atualizar status do pedido: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Endpoint para receber dados do Make.com
@app.route('/webhook/orders', methods=['POST'])
def receive_orders_from_make():
    """
    Recebe dados de pedidos do Make.com e armazena para consulta pelo Consumer
    """
    logger.info("Recebendo dados de pedidos do Make.com")
    try:
        # Obter os dados da requisição
        data = request.json
        
        # Validar dados
        is_valid, message = validate_order_data(data)
        if not is_valid:
            logger.error(f"Dados inválidos: {message}")
            return jsonify({"status": "error", "message": message}), 400
        
        # Armazenar o pedido no cache
        order_id = data.get('id')
        orders_cache[str(order_id)] = data
        
        # Criar um evento para polling
        event_id = str(uuid.uuid4())
        event = {
            "id": event_id,
            "orderId": order_id,
            "createdAt": datetime.now().isoformat(),
            "fullCode": "PLACED",
            "code": "PLC"
        }
        
        logger.info(f"Pedido recebido e armazenado: {order_id}")
        return jsonify({"status": "success", "message": "Pedido recebido com sucesso"})
    
    except Exception as e:
        logger.error(f"Erro ao receber pedido do Make.com: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
