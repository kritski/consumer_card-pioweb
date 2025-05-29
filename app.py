from flask import Flask, request, jsonify
from datetime import datetime, timedelta

app = Flask(__name__)

# Fila simples de pedidos em memória
PEDIDOS = {}

def transform_order_data(order):
    """Transforma o payload do CardápioWeb para o formato esperado pelo Consumer"""
    # Isso aqui ajusta conforme seu payload REAL (ajuste se faltar campo)
    pedido_id = str(order["id"])
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
                "method": order["payments"][0].get("payment_method", ""),
                "type": order["payments"][0].get("payment_type", ""),
                "currency": "BRL",
                "value": order["payments"][0].get("total", 0)
            }],
            "pending": 0,
            "prepaid": order["payments"][0].get("total", 0)
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

# Webhook que o Make envia os pedidos
@app.route('/webhook/orders', methods=['POST'])
def webhook_orders():
    order = request.json
    pedido = transform_order_data(order)
    PEDIDOS[pedido['id']] = pedido
    print(f"Pedido {pedido['id']} recebido e armazenado para integração!")
    return jsonify({"status": "success", "message": "Pedido processado com sucesso", "data": pedido})

# Consumer faz o polling buscando todos os pedidos pendentes
@app.route('/api/parceiro/polling', methods=['GET'])
def polling():
    pedidos_pendentes = list(PEDIDOS.values())
    print(f"Polling: {len(pedidos_pendentes)} pedidos pendentes!")
    return jsonify({"orders": pedidos_pendentes})

# Consumer consulta detalhes do pedido pelo ID
@app.route('/api/parceiro/order/<order_id>', methods=['GET'])
def detalhes_pedido(order_id):
    pedido = PEDIDOS.get(order_id)
    if pedido:
        print(f"Detalhes do pedido {order_id} retornados para o Consumer.")
        return jsonify(pedido)
    print(f"Tentativa de buscar pedido {order_id} não encontrado.")
    return jsonify({"error": "Pedido não encontrado"}), 404

# Consumer avisa que processou o pedido (POST), remove ele da fila
@app.route('/api/parceiro/order/<order_id>', methods=['POST'])
def marcar_integrado(order_id):
    if order_id in PEDIDOS:
        del PEDIDOS[order_id]
        print(f"Pedido {order_id} removido da lista de pendentes (integrado pelo Consumer).")
        return jsonify({"status": "success", "message": f"Pedido {order_id} integrado e removido da fila."})
    return jsonify({"error": "Pedido não encontrado"}), 404

if __name__ == "__main__":
    app.run()
