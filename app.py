from flask import Flask, request, jsonify, abort
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

# ---- CONFIG ----
CARDAPIOWEB_BASE = 'https://integracao.cardapioweb.com/api/partner/v1'
CARDAPIOWEB_TOKEN = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5'
CARDAPIOWEB_MERCHANT = '14104'

CONSUMER_API_TOKEN = 'pk_live_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy'

PEDIDOS = {}  # Banco simples, substitua por Redis/SQLite para produção

# ---- UTILS ----

def get_cardapioweb_order(order_id):
    """Busca detalhes de pedido pelo id diretamente no Cardápio Web"""
    url = f'{CARDAPIOWEB_BASE}/orders/{order_id}'
    headers = {'X-API-KEY': CARDAPIOWEB_TOKEN, 'Content-Type': 'application/json'}
    params = {'merchant_id': CARDAPIOWEB_MERCHANT}
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code == 200:
        return resp.json()
    else:
        return None

def update_cardapioweb_status(order_id, new_status):
    """Atualiza status do pedido via API CardápioWeb"""
    url = f'{CARDAPIOWEB_BASE}/orders/{order_id}/status'
    headers = {'X-API-KEY': CARDAPIOWEB_TOKEN, 'Content-Type': 'application/json'}
    data = {'status': new_status}
    resp = requests.post(url, headers=headers, json=data)
    return resp.status_code in (200, 204)

def transform_order_data(order):
    """Transforma cardapioweb para padrão Consumer"""
    # Adapte caso altere campos na especificação futura
    pedido_id = str(order["id"])
    payment = order["payments"][0] if order.get("payments") else {"payment_method":"", "payment_type":"", "total":0}
    return {
        "id": pedido_id,
        "displayId": str(order.get("display_id", "")),
        "orderType": order.get("order_type", "").upper(),
        "salesChannel": order.get("sales_channel", "").upper(),
        "orderTiming": order.get("order_timing", "").upper(),
        "createdAt": order.get("created_at", ""),
        "preparationStartDateTime": datetime.utcnow().isoformat() + "Z",
        "merchant": {
            "id": str(order.get("merchant_id", "")),
            "name": "Seu Restaurante"
        },
        "total": {
            "subTotal": 0,
            "deliveryFee": 0,
            "orderAmount": order.get("total", 0),
            "benefits": 0,
            "additionalFees": 0
        },
        "payments": {
            "methods": [{
                "method": payment.get("payment_method", ""),
                "type": payment.get("payment_type", ""),
                "currency": "BRL",
                "value": payment.get("total", 0)
            }],
            "pending": 0,
            "prepaid": payment.get("total", 0)
        },
        "customer": {
            "id": str(order["customer"].get("id", "")),
            "name": order["customer"].get("name", ""),
            "phone": {
                "number": order["customer"].get("phone", ""),
                "localizer": order["customer"].get("phone", ""),
                "localizerExpiration": (datetime.utcnow() + timedelta(days=1)).isoformat() + "Z"
            },
            "documentNumber": None
        },
        "delivery": {
            "mode": "EXPRESS",
            "deliveredBy": order.get("delivered_by", ""),
            "pickupCode": None,
            "deliveryDateTime": datetime.utcnow().isoformat() + "Z",
            "deliveryAddress": {
                "country": "Brasil",
                "state": order["delivery_address"].get("state", ""),
                "city": order["delivery_address"].get("city", ""),
                "postalCode": order["delivery_address"].get("postal_code", ""),
                "streetName": order["delivery_address"].get("street", ""),
                "streetNumber": order["delivery_address"].get("number", ""),
                "neighborhood": order["delivery_address"].get("neighborhood", ""),
                "complement": order["delivery_address"].get("complement", ""),
                "reference": order["delivery_address"].get("reference", "")
            }
        },
        "items": [
            {
                "id": str(item.get("item_id", "")),
                "externalCode": item.get("external_code", ""),
                "name": item.get("name", ""),
                "quantity": item.get("quantity", 0),
                "unitPrice": item.get("unit_price", 0),
                "totalPrice": item.get("total_price", 0),
                "observations": item.get("observation", ""),
                "options": item.get("options", [])
            } for item in order.get("items", [])
        ]
    }

def verify_consumer_token(request):
    """Verifica se header Xapikey OU Authorization é o da config"""
    token1 = request.headers.get("Xapikey")
    token2 = request.headers.get("Authorization")
    if token1 == CONSUMER_API_TOKEN or (token2 and token2.split()[-1] == CONSUMER_API_TOKEN):
        return True
    return False

# ---- ENDPOINTS PARA O CARCAPIO WEB CHAMAR VIA WEBHOOK ----
@app.route('/webhook/cardapioweb', methods=['POST'])
def webhook_novo_pedido():
    """Ponto de entrada do webhook do Cardápio Web para pedido novo/atualização"""
    order = request.json
    PEDIDOS[str(order["id"])] = transform_order_data(order)
    print(f"[Webhook] Pedido {order['id']} recebido/atualizado via webhook.")
    return jsonify({"status": "recebido"})

# ---- ENDPOINTS CHAMADOS PELO CONSUMER ----

@app.route('/api/parceiro/polling', methods=['GET'])
def api_polling():
    if not verify_consumer_token(request): return abort(401)
    # Retorna todos pedidos armazenados
    ordem = list(PEDIDOS.values())
    print(f"[Polling] {len(ordem)} pedidos para Consumer.")
    return jsonify({"orders": ordem})

@app.route('/api/parceiro/order/<order_id>', methods=['GET'])
def api_order_details(order_id):
    if not verify_consumer_token(request): return abort(401)
    pedido = PEDIDOS.get(order_id)
    if not pedido:
        # Busca na Cardapioweb (opcional, pode comentar caso não queira fallback)
        raw = get_cardapioweb_order(order_id)
        if not raw: return abort(404)
        pedido = transform_order_data(raw)
        PEDIDOS[order_id] = pedido
    return jsonify(pedido)

@app.route('/api/parceiro/order/<order_id>', methods=['POST'])
def api_update_status(order_id):
    if not verify_consumer_token(request): return abort(401)
    data = request.json
    new_status = data.get("status") or data.get("action") or None
    if not new_status:
        return abort(400)
    # Atualiza “no seu banco” (opcional)
    if order_id in PEDIDOS:
        PEDIDOS[order_id]['status'] = new_status
    # Atualiza no Cardápio Web
    ok = update_cardapioweb_status(order_id, new_status)
    print(f"Status de {order_id}: {new_status} enviado para Cardápio Web:
