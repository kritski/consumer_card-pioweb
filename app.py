from flask import Flask, request, jsonify, abort
from datetime import datetime, timedelta
import requests

app = Flask(__name__)

# === CONFIGURAÇÕES ===
CARDAPIOWEB_BASE = 'https://integracao.cardapioweb.com/api/partner/v1'
CARDAPIOWEB_TOKEN = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5'
CARDAPIOWEB_MERCHANT = '14104'
CONSUMER_API_TOKEN = 'pk_live_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy'

PEDIDOS = {}  # Em produção use Redis ou banco persistente

def transform_order_data(order):
    payment = order["payments"][0] if order.get("payments") else {"payment_method":"", "payment_type":"", "total":0}
    return {
        "id": str(order["id"]),
        "displayId": str(order.get("display_id", "")),
        "status": order.get("status", "PLACED"),
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
    token1 = request.headers.get("Xapikey")
    token2 = request.headers.get("Authorization")
    if token1 == CONSUMER_API_TOKEN or (token2 and token2.split()[-1] == CONSUMER_API_TOKEN):
        return True
    return False

def update_cardapioweb_status(order_id, status):
    url = f"{CARDAPIOWEB_BASE}/orders/{order_id}/status"
    headers = {'X-API-KEY': CARDAPIOWEB_TOKEN, 'Content-Type': 'application/json'}
    data = {'status': status}
    resp = requests.post(url, headers=headers, json=data)
    return resp.status_code in (200, 204)

# === WEBHOOK DO CARDAPIO WEB ===
@app.route('/webhook/cardapioweb', methods=['POST'])
def webhook_novo_pedido():
    raw = request.json
    print("DEBUG: Recebido no webhook:", raw)

    order_id = raw.get("order_id")
    if not order_id:
        print("Erro: payload sem 'order_id'.")
        return jsonify({"error": "Payload sem order_id", "debug": raw}), 400

    # Buscar detalhes completos do pedido no Cardápio Web
    url = f"{CARDAPIOWEB_BASE}/orders/{order_id}"
    headers = {'X-API-KEY': CARDAPIOWEB_TOKEN, 'Content-Type': 'application/json'}
    params = {'merchant_id': CARDAPIOWEB_MERCHANT}
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        print(f"Erro ao obter detalhes do pedido {order_id}: status {resp.status_code}")
        return jsonify({"error": f"Erro ao buscar detalhes do pedido {order_id}"}), 500

    order_full = resp.json()
    PEDIDOS[str(order_id)] = transform_order_data(order_full)
    print(f"[Webhook] Pedido {order_id} capturado e armazenado com sucesso.")
    return jsonify({"status": "recebido"})

# === ENDPOINT POLLING PARA O CONSUMER (agora no novo formato) ===
@app.route('/api/parceiro/polling', methods=['GET'])
def api_polling():
    if not verify_consumer_token(request): return abort(401)
    # O Consumer espera o campo items
    items = []
    for order_id, pedido in PEDIDOS.items():
        items.append({
            "id": str(order_id),
            "orderId": str(order_id),
            "createdAt": pedido.get("createdAt"),
            "fullCode": pedido.get("status", "PLACED"),
            "code": "PLC"  # Código simplificado para status PLACED; adapte conforme necessário
        })
    print("[DEBUG][POLLING] ids entregues para o consumer:", [i["id"] for i in items])
    return jsonify({
        "items": items,
        "statusCode": 0,
        "reasonPhrase": None
    })

# === DETALHES DO PEDIDO PARA O CONSUMER ===
@app.route('/api/parceiro/order/<order_id>', methods=['GET'])
def api_order_details(order_id):
    if not verify_consumer_token(request): return abort(401)
    pedido = PEDIDOS.get(order_id)
    if not pedido:
        print(f"Tentativa de buscar pedido {order_id} não encontrado.")
        return abort(404)
    return jsonify(pedido)

# === ATUALIZAÇÃO DE STATUS (POST) ===
@app.route('/api/parceiro/order/<order_id>', methods=['POST'])
def api_update_status(order_id):
    if not verify_consumer_token(request): return abort(401)
    data = request.json
    new_status = data.get("status") or data.get("action")
    if not new_status:
        return abort(400)
    if order_id in PEDIDOS:
        PEDIDOS[order_id]['status'] = new_status
    ok = update_cardapioweb_status(order_id, new_status)
    return jsonify({"status": "CardapioWeb atualizado" if ok else "Erro CardapioWeb"})

if __name__ == "__main__":
    app.run()
