from flask import Flask, request, jsonify, abort
from datetime import datetime
import requests

app = Flask(__name__)

# Configurações Cardápio Web
CARDAPIOWEB_BASE = 'https://integracao.cardapioweb.com/api/partner/v1'
CARDAPIOWEB_TOKEN = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5'
CARDAPIOWEB_MERCHANT = '14104'
CONSUMER_API_TOKEN = 'pk_live_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy'

# Banco simples (em memória)
PEDIDOS_PENDENTES = {}

def transform_order_data(order):
    payment = order["payments"][0] if order.get("payments") else {"payment_method": "", "payment_type": "", "total": 0}
    address = order.get("delivery_address") or {}
    return {
        "id": str(order.get("id")),
        "displayId": str(order.get("display_id", "")),
        "orderType": order.get("order_type", "").upper(),
        "salesChannel": order.get("sales_channel", "").upper(),
        "orderTiming": order.get("order_timing", "").upper(),
        "createdAt": order.get("created_at", datetime.utcnow().isoformat()),
        "customer": order.get("customer", {}),
        "delivery": {
            "mode": order.get("delivered_by") or "EXPRESS",
            "deliveredBy": order.get("delivered_by", ""),
            "pickupCode": None,
            "deliveryDateTime": order.get("created_at", datetime.utcnow().isoformat()),
            "deliveryAddress": {
                "country": "Brasil",
                "state": address.get("state", ""),
                "city": address.get("city", ""),
                "postalCode": address.get("postal_code", ""),
                "streetName": address.get("street", ""),
                "streetNumber": address.get("number", ""),
                "neighborhood": address.get("neighborhood", ""),
                "complement": address.get("complement", ""),
                "reference": address.get("reference", "")
            }
        },
        "items": order.get("items", []),
        "merchant": {
            "id": str(order.get("merchant_id", "")),
            "name": "Seu Restaurante"
        },
        "total": {
            "subTotal": 0,
            "deliveryFee": order.get("delivery_fee", 0),
            "orderAmount": order.get("total", 0),
            "benefits": 0,
            "additionalFees": 0
        },
        "payments": {
            "methods": [{
                "method": payment.get("payment_method", ""),
                "type": payment.get("payment_type", ""),
                "value": payment.get("total", 0),
                "currency": "BRL",
            }],
            "pending": 0,
            "prepaid": payment.get("total", 0)
        },
    }

def verify_token(request):
    token1 = request.headers.get("Xapikey")
    token2 = request.headers.get("Authorization")
    if token1 == CONSUMER_API_TOKEN or (token2 and token2.split()[-1] == CONSUMER_API_TOKEN):
        return True
    return False

# --- Aceita ambos endpoints: compatível com o Consumer e o Cardápio ---
@app.route('/webhook/orders', methods=['POST'])
@app.route('/webhook/cardapioweb', methods=['POST'])
def webhook_orders():
    event = request.get_json()
    print("DEBUG: Recebido no webhook:", event)

    # Primeiro tenta 'id' (Make), se não, tenta 'order_id' (Cardápio Web)
    order_id = event.get("id") or event.get("order_id")
    if not order_id:
        return jsonify({"error": "Payload inesperado, sem id/order_id", "raw": event}), 400

    # Se só tiver order_id, buscar detalhes completos na API CardápioWeb
    if "order_id" in event and len(event.keys()) <= 6:
        url = f"{CARDAPIOWEB_BASE}/orders/{order_id}"
        headers = {"X-API-KEY": CARDAPIOWEB_TOKEN, "Content-Type": "application/json"}
        params = {"merchant_id": CARDAPIOWEB_MERCHANT}
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            print("Erro ao buscar detalhes para o pedido:", order_id, "Status:", resp.status_code)
            return jsonify({"error": "Falha ao obter detalhes"}), 500
        order = resp.json()
    else:
        order = event

    pedido_transformado = transform_order_data(order)
    PEDIDOS_PENDENTES[str(order_id)] = pedido_transformado
    print(f"Pedido {order_id} capturado via webhook e armazenado.")
    return jsonify({"success": True})

@app.route('/api/parceiro/polling', methods=['GET'])
def polling():
    if not verify_token(request): return abort(401)
    pedidos = list(PEDIDOS_PENDENTES.values())
    print(f"Polling: {len(pedidos)} pedidos pendentes para integração.")
    return jsonify({"orders": pedidos})

@app.route('/api/parceiro/order/<order_id>', methods=['GET'])
def get_order(order_id):
    if not verify_token(request): return abort(401)
    pedido = PEDIDOS_PENDENTES.get(order_id)
    if pedido:
        return jsonify(pedido)
    return jsonify({"error": "Pedido não encontrado"}), 404

@app.route('/api/parceiro/order/<order_id>', methods=['POST'])
def integrar_order(order_id):
    if not verify_token(request): return abort(401)
    if order_id in PEDIDOS_PENDENTES:
        PEDIDOS_PENDENTES.pop(order_id)
        print(f"Pedido {order_id} removido da fila/considerado integrado.")
        return jsonify({"success": True})
    return jsonify({"error": "Pedido não encontrado"}), 404

if __name__ == '__main__':
    app.run()
