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
import traceback

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
MAKE_WEBHOOK_URL = os.environ.get('MAKE_WEBHOOK_URL', 'https://hook.us2.make.com/t9b65xppmlcycvdtlpwx1wfzuo2i6qg6')
API_TOKEN = os.environ.get('API_TOKEN', 'pk_live_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy')

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
    xapikey_header = request.headers.get('Xapikey')
    
    logger.info(f"Auth header: {auth_header}")
    logger.info(f"Xapikey header: {xapikey_header}")
    
    # Verificar primeiro o header Authorization
    if auth_header:
        is_valid, message = verify_token_auth(auth_header)
        if is_valid:
            logger.info("Token validado com sucesso")
            return
    
    # Se não tiver Authorization válido, verificar Xapikey
    if xapikey_header and xapikey_header == API_TOKEN:
        logger.info("Token Xapikey validado com sucesso")
        return
    
    # Se nenhum dos dois for válido, retornar erro
    return jsonify({'error': 'Token inválido ou não fornecido'}), 401

# Função para validar os dados do pedido
def validate_order_data(data):
    """
    Valida os dados do pedido recebidos
    """
    # Verificar se data é um dicionário
    if not isinstance(data, dict):
        return False, f"Dados inválidos: esperado um dicionário, recebido {type(data)}"
    
    # Verificar campos obrigatórios com tratamento de erro
    required_fields = ['id', 'created_at', 'order_type']
    missing_fields = []
    
    for field in required_fields:
        if field not in data:
            # Verificar se o campo existe com notação de ponto
            found = False
            for key in data.keys():
                if key == field or key.startswith(field + '.') or key == field.replace('_', '.'):
                    found = True
                    break
            
            if not found:
                missing_fields.append(field)
    
    if missing_fields:
        return False, f"Campos obrigatórios ausentes: {', '.join(missing_fields)}"
    
    return True, "Dados válidos"

# Função para extrair dados do bundle do Make.com
def extract_data_from_bundle(bundle_data):
    """
    Extrai dados do bundle do Make.com, que pode ser um array ou objeto
    """
    logger.info(f"Extraindo dados do bundle: {type(bundle_data)}")
    
    # Se for um array, processar cada item
    if isinstance(bundle_data, list):
        # Verificar se o array contém dados
        if not bundle_data:
            logger.warning("Bundle vazio recebido")
            return None
        
        # Verificar se o primeiro item é um dicionário com campo 'data'
        if isinstance(bundle_data[0], dict) and 'data' in bundle_data[0]:
            # Formato comum do Make.com: [{data: {...}}]
            data = bundle_data[0].get('data')
            logger.info(f"Dados extraídos do campo 'data': {type(data)}")
            return data
        elif isinstance(bundle_data[0], dict) and 'text' in bundle_data[0]:
            # Formato do Text Aggregator: [{text: "json string"}]
            text = bundle_data[0].get('text')
            try:
                data = json.loads(text)
                logger.info(f"Dados extraídos do campo 'text' (Text Aggregator): {type(data)}")
                return data
            except:
                logger.error("Erro ao parsear JSON do campo 'text'")
                return bundle_data[0]
        else:
            # Retornar o primeiro item do array
            logger.info(f"Dados extraídos do primeiro item do array: {type(bundle_data[0])}")
            return bundle_data[0]
    
    # Se for um dicionário, verificar se tem campo 'data'
    elif isinstance(bundle_data, dict):
        if 'data' in bundle_data:
            logger.info(f"Dados extraídos do campo 'data' do objeto: {type(bundle_data['data'])}")
            return bundle_data['data']
        elif 'text' in bundle_data:
            # Formato do Text Aggregator: {text: "json string"}
            text = bundle_data.get('text')
            try:
                data = json.loads(text)
                logger.info(f"Dados extraídos do campo 'text' (Text Aggregator): {type(data)}")
                return data
            except:
                logger.error("Erro ao parsear JSON do campo 'text'")
                return bundle_data
        else:
            # Retornar o próprio dicionário
            logger.info("Usando o próprio dicionário como dados")
            return bundle_data
    
    # Se não for nem array nem dicionário, retornar como está
    logger.warning(f"Formato de dados não reconhecido: {type(bundle_data)}")
    return bundle_data

# Função para transformar dados com notação de ponto para objetos aninhados
def transform_dotted_to_nested(data):
    """
    Transforma dados com notação de ponto para objetos aninhados
    Ex: {"customer.name": "João"} -> {"customer": {"name": "João"}}
    """
    result = {}
    
    for key, value in data.items():
        if '.' in key:
            parts = key.split('.')
            current = result
            
            # Lidar com arrays (ex: items[0].name)
            for i, part in enumerate(parts):
                array_index = None
                
                # Verificar se é um item de array (ex: items[0])
                if '[' in part and ']' in part:
                    array_name = part.split('[')[0]
                    array_index = int(part.split('[')[1].split(']')[0])
                    part = array_name
                
                # Se não é o último elemento do caminho
                if i < len(parts) - 1:
                    # Criar objeto aninhado se não existir
                    if part not in current:
                        if array_index is not None:
                            current[part] = []
                        else:
                            current[part] = {}
                    
                    # Navegar para o próximo nível
                    if array_index is not None:
                        # Garantir que o array tenha elementos suficientes
                        while len(current[part]) <= array_index:
                            current[part].append({})
                        current = current[part][array_index]
                    else:
                        current = current[part]
                else:
                    # Último elemento do caminho, atribuir o valor
                    if array_index is not None:
                        if part not in current:
                            current[part] = []
                        while len(current[part]) <= array_index:
                            current[part].append({})
                        current[part][array_index] = value
                    else:
                        current[part] = value
        else:
            # Chave simples sem pontos
            result[key] = value
    
    return result

# Função para transformar os dados do CardápioWeb para o formato do Consumer
def transform_to_consumer_format(cardapio_order):
    """
    Transforma o formato do CardápioWeb para o formato do Consumer
    """
    # Log dos dados recebidos para depuração
    logger.info(f"Dados recebidos para transformação: {json.dumps(cardapio_order, default=str)}")
    
    # Se os dados vierem em um array, pegar o primeiro item
    if isinstance(cardapio_order, list) and len(cardapio_order) > 0:
        cardapio_order = cardapio_order[0]
    
    # Se os dados vierem como string JSON, converter para dicionário
    if isinstance(cardapio_order, str):
        try:
            cardapio_order = json.loads(cardapio_order)
        except json.JSONDecodeError:
            logger.error("Erro ao decodificar string JSON")
            return None
    
    # Se os dados vierem com notação de ponto, transformar para objetos aninhados
    if isinstance(cardapio_order, dict) and any('.' in key for key in cardapio_order.keys()):
        cardapio_order = transform_dotted_to_nested(cardapio_order)
    
    # Garantir que temos os campos mínimos necessários
    required_fields = ['id', 'created_at', 'order_type']
    for field in required_fields:
        if field not in cardapio_order and not any(key.startswith(field + '.') for key in cardapio_order.keys()):
            # Tentar encontrar campos equivalentes
            if field == 'created_at' and 'createdAt' in cardapio_order:
                cardapio_order['created_at'] = cardapio_order['createdAt']
            elif field == 'order_type' and 'orderType' in cardapio_order:
                cardapio_order['order_type'] = cardapio_order['orderType']
            else:
                logger.error(f"Campo obrigatório ausente: {field}")
                return None
    
    # Extrair dados do cliente
    customer = cardapio_order.get('customer', {})
    if isinstance(customer, str):
        try:
            customer = json.loads(customer)
        except:
            customer = {}
    
    customer_phone = customer.get('phone', {})
    if isinstance(customer_phone, str):
        phone_number = customer_phone
        customer_phone = {'number': phone_number}
    else:
        phone_number = customer_phone.get('number', '')
    
    # Extrair dados de pagamento
    payments = cardapio_order.get('payments', {})
    if isinstance(payments, str):
        try:
            payments = json.loads(payments)
        except:
            payments = {}
    
    # CORREÇÃO: Tratar payments como lista ou dicionário
    payment_methods = []
    payment_pending = 0
    
    # Se payments for uma lista, extrair o primeiro item
    if isinstance(payments, list):
        logger.info("Campo 'payments' recebido como lista")
        if payments:
            first_payment = payments[0]
            # Extrair método de pagamento do primeiro item da lista
            payment_method = first_payment.get('payment_method', first_payment.get('method', 'ONLINE'))
            payment_type = first_payment.get('payment_type', first_payment.get('type', 'CREDIT'))
            payment_value = float(first_payment.get('total', first_payment.get('value', 0)))
            payment_currency = first_payment.get('currency', 'BRL')
            
            payment_methods = [{
                'method': payment_method,
                'type': payment_type,
                'currency': payment_currency,
                'value': payment_value
            }]
    else:
        # Tratar como dicionário
        logger.info("Campo 'payments' recebido como dicionário")
        payment_methods = payments.get('methods', [{}])
        payment_pending = float(payments.get('pending', 0))
        
        if isinstance(payment_methods, str):
            try:
                payment_methods = json.loads(payment_methods)
            except:
                payment_methods = [{}]
        elif not isinstance(payment_methods, list):
            payment_methods = [payment_methods]
    
    # Extrair informações do primeiro método de pagamento
    payment_info = payment_methods[0] if payment_methods else {}
    payment_method = payment_info.get('method', 'ONLINE')
    payment_type = payment_info.get('type', 'CREDIT')
    
    # Calcular valores
    total_info = cardapio_order.get('total', {})
    if isinstance(total_info, str):
        try:
            total_info = json.loads(total_info)
        except:
            total_info = {}
    elif isinstance(total_info, (int, float)):
        # Se total for um número, criar um dicionário com esse valor
        total_value = float(total_info)
        total_info = {'orderAmount': total_value}
    
    # Extrair valores de subtotal, delivery_fee e total
    subtotal = 0
    delivery_fee = 0
    total = 0
    
    # Tentar extrair subtotal
    if isinstance(total_info, dict):
        subtotal = float(total_info.get('subTotal', total_info.get('subtotal', 0)))
    
    # Tentar extrair delivery_fee
    delivery_fee = float(cardapio_order.get('delivery_fee', 0))
    
    # Tentar extrair total
    if isinstance(total_info, dict):
        total = float(total_info.get('orderAmount', total_info.get('order_amount', 0)))
    else:
        # Tentar obter total diretamente do pedido
        total = float(cardapio_order.get('total', 0))
    
    # Se o total for zero, tentar calcular a partir de outros campos
    if total == 0:
        total = subtotal + delivery_fee
    
    # Criar objeto no formato do Consumer
    now = datetime.now(timezone.utc)
    expiration_time = now + timedelta(hours=24)
    
    # Extrair dados de entrega
    delivery_info = cardapio_order.get('delivery', {})
    if isinstance(delivery_info, str):
        try:
            delivery_info = json.loads(delivery_info)
        except:
            delivery_info = {}
    
    # Extrair endereço de entrega
    delivery_address = {}
    
    # Verificar se temos delivery_address no objeto delivery
    if isinstance(delivery_info, dict) and 'deliveryAddress' in delivery_info:
        delivery_address = delivery_info.get('deliveryAddress', {})
    # Verificar se temos delivery_address diretamente no pedido
    elif 'delivery_address' in cardapio_order:
        delivery_address = cardapio_order.get('delivery_address', {})
    
    if isinstance(delivery_address, str):
        try:
            delivery_address = json.loads(delivery_address)
        except:
            delivery_address = {}
    
    # Extrair dados do estabelecimento
    merchant = cardapio_order.get('merchant', {})
    if isinstance(merchant, str):
        try:
            merchant = json.loads(merchant)
        except:
            merchant = {}
    
    # Se merchant for vazio, tentar extrair merchant_id e merchant_name
    if not merchant:
        merchant_id = cardapio_order.get('merchant_id', '')
        merchant_name = cardapio_order.get('merchant_name', 'Seu Restaurante')
        merchant = {'id': merchant_id, 'name': merchant_name}
    
    # Extrair itens
    items = cardapio_order.get('items', [])
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except:
            items = []
    elif not isinstance(items, list):
        # Se items não for uma lista, tentar converter
        items = [items]
    
    # Construir o objeto de resposta no formato do Consumer
    consumer_format = {
        "id": str(cardapio_order.get('id', '')),
        "displayId": str(cardapio_order.get('displayId', cardapio_order.get('display_id', ''))),
        "orderType": str(cardapio_order.get('orderType', cardapio_order.get('order_type', 'DELIVERY'))).upper(),
        "salesChannel": str(cardapio_order.get('salesChannel', cardapio_order.get('sales_channel', 'MARKETPLACE'))).upper(),
        "orderTiming": str(cardapio_order.get('orderTiming', cardapio_order.get('order_timing', 'ASAP'))).upper(),
        "createdAt": cardapio_order.get('createdAt', cardapio_order.get('created_at', now.isoformat())),
        "preparationStartDateTime": cardapio_order.get('preparationStartDateTime', now.isoformat()),
        "merchant": {
            "id": str(merchant.get('id', '')),
            "name": merchant.get('name', 'Seu Restaurante')
        },
        "total": {
            "subTotal": subtotal,
            "deliveryFee": delivery_fee,
            "orderAmount": total,
            "benefits": float(total_info.get('benefits', 0)) if isinstance(total_info, dict) else 0,
            "additionalFees": float(total_info.get('additionalFees', 0)) if isinstance(total_info, dict) else 0
        },
        "payments": {
            "methods": [
                {
                    "method": payment_method,
                    "type": payment_type,
                    "currency": payment_info.get('currency', 'BRL'),
                    "value": float(payment_info.get('value', total))
                }
            ],
            "pending": payment_pending,
            "prepaid": total
        },
        "customer": {
            "id": str(customer.get('id', '')),
            "name": customer.get('name', ''),
            "phone": {
                "number": phone_number,
                "localizer": customer_phone.get('localizer', phone_number),
                "localizerExpiration": customer_phone.get('localizerExpiration', expiration_time.isoformat())
            },
            "documentNumber": customer.get('documentNumber')
        }
    }
    
    # Adicionar dados de entrega se for um pedido de delivery
    order_type = str(cardapio_order.get('orderType', cardapio_order.get('order_type', ''))).lower()
    if order_type == 'delivery':
        # Mapear campos do endereço de entrega
        street_name = delivery_address.get('streetName', delivery_address.get('street_name', delivery_address.get('street', '')))
        street_number = delivery_address.get('streetNumber', delivery_address.get('street_number', delivery_address.get('number', '')))
        postal_code = delivery_address.get('postalCode', delivery_address.get('postal_code', delivery_address.get('zip_code', '')))
        
        consumer_format["delivery"] = {
            "mode": delivery_info.get('mode', 'EXPRESS'),
            "deliveredBy": delivery_info.get('deliveredBy', cardapio_order.get('delivered_by', 'MERCHANT')),
            "pickupCode": delivery_info.get('pickupCode'),
            "deliveryDateTime": delivery_info.get('deliveryDateTime', now.isoformat()),
            "deliveryAddress": {
                "country": delivery_address.get('country', 'Brasil'),
                "state": delivery_address.get('state', ''),
                "city": delivery_address.get('city', ''),
                "postalCode": postal_code,
                "streetName": street_name,
                "streetNumber": street_number,
                "neighborhood": delivery_address.get('neighborhood', ''),
                "complement": delivery_address.get('complement'),
                "reference": delivery_address.get('reference')
            }
        }
    
    # Adicionar itens
    formatted_items = []
    for item in items:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except:
                continue
        
        # Garantir que temos os campos mínimos necessários para o item
        item_id = str(item.get('id', item.get('item_id', '')))
        item_name = item.get('name', '')
        item_quantity = int(item.get('quantity', 1))
        item_unit_price = float(item.get('unitPrice', item.get('unit_price', 0)))
        item_total_price = float(item.get('totalPrice', item.get('total_price', item_unit_price * item_quantity)))
        
        # Extrair opções do item
        options = item.get('options', [])
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except:
                options = []
        
        # Criar objeto de item no formato do Consumer
        formatted_item = {
            "id": item_id,
            "externalCode": item.get('externalCode', item.get('external_code')),
            "name": item_name,
            "quantity": item_quantity,
            "unitPrice": item_unit_price,
            "totalPrice": item_total_price,
            "observations": item.get('observation', item.get('observations')),
            "options": options
        }
        
        formatted_items.append(formatted_item)
    
    consumer_format["items"] = formatted_items
    
    # Log do objeto transformado para depuração
    logger.info(f"Objeto transformado: {json.dumps(consumer_format, default=str)}")
    
    return consumer_format

# Rota de saúde para verificar se a API está funcionando
@app.route('/health', methods=['GET'])
def health_check():
    logger.info("Health check solicitado")
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

# Endpoint para receber pedidos do Make.com
@app.route('/webhook/orders', methods=['POST'])
def receive_orders_from_make():
    logger.info("Recebendo dados do Make.com")
    try:
        # Log do corpo da requisição para depuração
        request_data = request.get_data(as_text=True)
        logger.info(f"Dados brutos recebidos: {request_data}")
        
        # Verificar se o corpo da requisição está vazio
        if not request_data or request_data.isspace():
            logger.error("Corpo da requisição vazio")
            return jsonify({'status': 'error', 'message': 'Corpo da requisição vazio'}), 400
        
        # Tentar obter os dados como JSON
        try:
            # Tentar obter os dados diretamente do request
            data = request.json
            logger.info(f"Dados JSON parseados do request: {type(data)}")
        except Exception as e:
            logger.error(f"Erro ao parsear JSON do request: {str(e)}")
            # Tentar parsear manualmente
            try:
                data = json.loads(request_data)
                logger.info(f"Dados JSON parseados manualmente: {type(data)}")
            except Exception as json_e:
                logger.error(f"Falha ao parsear JSON manualmente: {str(json_e)}")
                return jsonify({'status': 'error', 'message': 'Formato JSON inválido'}), 400
        
        # Extrair dados do bundle do Make.com
        extracted_data = extract_data_from_bundle(data)
        if extracted_data is None:
            logger.error("Não foi possível extrair dados do bundle")
            return jsonify({'status': 'error', 'message': 'Não foi possível extrair dados do bundle'}), 400
        
        logger.info(f"Dados extraídos do bundle: {type(extracted_data)}")
        
        # Se os dados extraídos forem um array, processar cada item
        if isinstance(extracted_data, list):
            results = []
            for item in extracted_data:
                # Transformar os dados para o formato do Consumer
                consumer_data = transform_to_consumer_format(item)
                if consumer_data:
                    # Armazenar o pedido em cache para consultas futuras
                    order_id = consumer_data['id']
                    orders_cache[order_id] = consumer_data
                    results.append({
                        'id': order_id,
                        'status': 'success',
                        'message': 'Pedido processado com sucesso'
                    })
                else:
                    results.append({
                        'status': 'error',
                        'message': 'Falha na transformação de dados'
                    })
            
            # Retornar os resultados
            logger.info(f"Processados {len(results)} pedidos do bundle")
            return jsonify({
                'status': 'success',
                'message': f'Processados {len(results)} pedidos',
                'results': results
            })
        else:
            # Transformar os dados para o formato do Consumer
            consumer_data = transform_to_consumer_format(extracted_data)
            if not consumer_data:
                logger.error("Falha na transformação de dados")
                return jsonify({'status': 'error', 'message': 'Falha na transformação de dados'}), 400
            
            # Armazenar o pedido em cache para consultas futuras
            order_id = consumer_data['id']
            orders_cache[order_id] = consumer_data
            
            # Retornar os dados transformados
            logger.info(f"Pedido {order_id} processado com sucesso")
            return jsonify({
                'status': 'success',
                'message': 'Pedido processado com sucesso',
                'data': consumer_data
            })
    
    except Exception as e:
        logger.error(f"Erro ao processar pedido: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'status': 'error', 'message': str(e)}), 500

# Endpoint de polling - Consumer busca pedidos novos
@app.route('/api/parceiro/polling', methods=['GET'])
def polling():
    """
    Endpoint para polling de pedidos pelo Consumer
    """
    try:
        logger.info("Polling solicitado pelo Consumer")
        
        # Preparar payload para o Make.com
        payload = {
            "action": "polling",
            "timestamp": datetime.now().isoformat(),
            "merchant_id": "14104"  # ID do merchant no CardápioWeb
        }
        
        logger.info(f"Enviando payload para Make.com: {payload}")
        
        # Chamar o webhook do Make.com para buscar pedidos
        response = requests.post(
            MAKE_WEBHOOK_URL,
            json=payload,
            headers={'Content-Type': 'application/json'}
        )
        
        logger.info(f"Resposta do Make.com: Status {response.status_code}")
        logger.info(f"Resposta do Make.com: Headers {dict(response.headers)}")
        logger.info(f"Resposta do Make.com: Body {response.text}")
        
        if response.status_code != 200:
            logger.error(f"Erro ao buscar pedidos no Make.com: {response.status_code}")
            # Retornar array vazio em vez de erro para o Consumer
            return jsonify([])
        
        # Processar a resposta do Make.com
        try:
            make_data = response.json()
            logger.info(f"Dados recebidos do Make.com: {make_data}")
            
            # Verificar se os dados estão no formato esperado pelo Consumer
            # O Consumer espera um array de objetos, mesmo que vazio
            if not isinstance(make_data, list):
                logger.warning("Dados do Make.com não estão em formato de array, convertendo...")
                
                # Se make_data for um objeto com campo 'orders', usar esse campo
                if isinstance(make_data, dict) and 'orders' in make_data:
                    make_data = make_data.get('orders', [])
                # Se make_data for um objeto com campo 'data', usar esse campo
                elif isinstance(make_data, dict) and 'data' in make_data:
                    data_field = make_data.get('data', {})
                    # Se data_field for um objeto com campo 'items', usar esse campo
                    if isinstance(data_field, dict) and 'items' in data_field:
                        make_data = data_field.get('items', [])
                    else:
                        make_data = [data_field] if data_field else []
                # Se make_data for um objeto sem campos especiais, converter para array
                elif make_data:
                    make_data = [make_data]
                else:
                    make_data = []
            
            # Formatar os dados no formato esperado pelo Consumer
            polling_items = []
            for item in make_data:
                # Garantir que cada item tenha os campos obrigatórios
                if isinstance(item, dict):
                    order_id = str(item.get('id', str(uuid.uuid4())))
                    polling_item = {
                        "id": order_id,
                        "orderId": order_id,
                        "createdAt": item.get('createdAt', item.get('created_at', datetime.now().isoformat())),
                        "fullCode": "PLACED",
                        "code": "PLC",
                        "statusCode": 0
                    }
                    polling_items.append(polling_item)
            
            logger.info(f"Retornando {len(polling_items)} itens para o Consumer")
            # Retornar array de itens diretamente, sem encapsular em objeto
            return jsonify(polling_items)
        
        except Exception as e:
            logger.error(f"Erro ao processar resposta do Make.com: {str(e)}")
            logger.error(traceback.format_exc())
            # Retornar array vazio em vez de erro para o Consumer
            return jsonify([])
    
    except Exception as e:
        logger.error(f"Erro ao fazer polling: {str(e)}")
        logger.error(traceback.format_exc())
        # Retornar array vazio em vez de erro para o Consumer
        return jsonify([])

# Endpoint de detalhes do pedido - Consumer busca detalhes de um pedido específico
@app.route('/api/parceiro/order/<order_id>', methods=['GET'])
def get_order_details(order_id):
    logger.info(f"Detalhes solicitados para pedido: {order_id}")
    try:
        # Verificar se o pedido está em cache
        if order_id in orders_cache:
            logger.info(f"Pedido {order_id} encontrado em cache")
            return jsonify(orders_cache[order_id])
        
        # Se não estiver em cache, buscar no Make.com
        response = requests.post(
            MAKE_WEBHOOK_URL,
            json={
                'action': 'get_order_details',
                'order_id': order_id,
                'timestamp': datetime.now().isoformat(),
                'merchant_id': '14104'  # ID do merchant no CardápioWeb
            },
            headers={'Content-Type': 'application/json'}
        )
        
        logger.info(f"Resposta do Make.com: Status {response.status_code}")
        logger.info(f"Resposta do Make.com: Headers {dict(response.headers)}")
        logger.info(f"Resposta do Make.com: Body {response.text}")
        
        if response.status_code != 200:
            logger.error(f"Erro ao buscar detalhes do pedido no Make.com: {response.status_code}")
            return jsonify({}), 404
        
        # Processar a resposta do Make.com
        try:
            make_data = response.json()
            logger.info(f"Dados recebidos do Make.com: {make_data}")
            
            # Se make_data for um objeto com campo 'data', usar esse campo
            if isinstance(make_data, dict) and 'data' in make_data:
                make_data = make_data.get('data', {})
            
            # Transformar os dados para o formato do Consumer
            consumer_data = transform_to_consumer_format(make_data)
            if not consumer_data:
                logger.error(f"Falha na transformação de dados para o pedido {order_id}")
                return jsonify({}), 404
            
            # Armazenar o pedido em cache para consultas futuras
            orders_cache[order_id] = consumer_data
            
            # Retornar os dados transformados
            logger.info(f"Detalhes do pedido {order_id} retornados com sucesso")
            return jsonify(consumer_data)
        
        except Exception as e:
            logger.error(f"Erro ao processar resposta do Make.com: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({}), 404
    
    except Exception as e:
        logger.error(f"Erro ao obter detalhes do pedido: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({}), 500

# Endpoint de mudança de status - Consumer atualiza o status de um pedido
@app.route('/api/parceiro/order/<order_id>', methods=['POST'])
def update_order_status(order_id):
    logger.info(f"Atualização de status solicitada para: {order_id}")
    try:
        # Obter os dados da requisição
        status_data = request.json
        logger.info(f"Dados de status recebidos: {status_data}")
        
        # Chamar o webhook do Make.com para atualizar o status do pedido
        response = requests.post(
            MAKE_WEBHOOK_URL,
            json={
                'action': 'update_order_status',
                'order_id': order_id,
                'status': status_data.get('status'),
                'timestamp': datetime.now().isoformat(),
                'merchant_id': '14104'  # ID do merchant no CardápioWeb
            },
            headers={'Content-Type': 'application/json'}
        )
        
        logger.info(f"Resposta do Make.com: Status {response.status_code}")
        logger.info(f"Resposta do Make.com: Headers {dict(response.headers)}")
        logger.info(f"Resposta do Make.com: Body {response.text}")
        
        if response.status_code != 200:
            logger.error(f"Erro ao atualizar status no Make.com: {response.status_code}")
            return jsonify({'error': 'Erro ao atualizar status no Make.com'}), 500
        
        # Retornar sucesso
        logger.info(f"Status do pedido {order_id} atualizado com sucesso")
        return jsonify({'status': 'success', 'message': 'Status atualizado com sucesso'})
    
    except Exception as e:
        logger.error(f"Erro ao atualizar status do pedido: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Endpoint para receber detalhes do pedido do Consumer
@app.route('/api/infoOrderDetail', methods=['POST'])
def receive_order_details():
    logger.info("Recebendo detalhes do pedido do Consumer")
    try:
        # Obter os dados da requisição
        order_details = request.json
        logger.info(f"Detalhes do pedido recebidos: {order_details}")
        
        # Chamar o webhook do Make.com para enviar os detalhes do pedido
        response = requests.post(
            MAKE_WEBHOOK_URL,
            json={
                'action': 'receive_order_details',
                'order_details': order_details,
                'timestamp': datetime.now().isoformat(),
                'merchant_id': '14104'  # ID do merchant no CardápioWeb
            },
            headers={'Content-Type': 'application/json'}
        )
        
        logger.info(f"Resposta do Make.com: Status {response.status_code}")
        logger.info(f"Resposta do Make.com: Headers {dict(response.headers)}")
        logger.info(f"Resposta do Make.com: Body {response.text}")
        
        if response.status_code != 200:
            logger.error(f"Erro ao enviar detalhes para o Make.com: {response.status_code}")
            return jsonify({'error': 'Erro ao enviar detalhes para o Make.com'}), 500
        
        # Retornar sucesso
        logger.info("Detalhes do pedido enviados com sucesso")
        return jsonify({'status': 'success', 'message': 'Detalhes do pedido recebidos com sucesso'})
    
    except Exception as e:
        logger.error(f"Erro ao receber detalhes do pedido: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
