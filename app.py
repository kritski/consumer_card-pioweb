from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

PEDIDOS_PENDENTES = {}

def transform_order_data(order):
    # Retorne todos os campos do pedido, mesmo que vazios
    return {
        "id": str(order.get("id")),
        "displayId": str(order.get("display_id", "")),
        "orderType": order.get("order_type", "").upper(),
        "salesChannel": order.get("sales_channel", "").upper(),
        "orderTiming": order.get("order_timing", "").upper(),
        "createdAt": order.get("created_at", datetime.utcnow().isoformat()),
        "customer": order.get("customer", {}),
        "delivery": order.get("delivery", {}),
        "items": order.get("items", []),
        "merchant": order.get("merchant", {})
        # Adicione outros campos conforme usados no modelo do Make
    }

@app.route('/webhook/orders', methods=['POST'])
def webhook_orders():
    order = request.get_json()
    pedido_id = str(order["id"])
    pedido_transformado = transform_order_data(order)
    PEDIDOS_PENDENTES[pedido_id] = pedido_transformado
    print(f"Pedido {pedido_id} armazenado.")
    return jsonify({"success": True})

@app.route('/api/parceiro/polling', methods=['GET'])
def polling():
    lista = list(PEDIDOS_PENDENTES.values())
    print(f"Polling: {len(lista)} pedidos pendentes para integração.")
    return jsonify({"orders": lista})

@app.route('/api/parceiro/order/<order_id>', methods=['GET'])
def get_order(order_id):
    pedido = PEDIDOS_PENDENTES.get(order_id)
    if pedido:
        return jsonify(pedido)
    return jsonify({"error": "Pedido não encontrado"}), 404

@app.route('/api/parceiro/order/<order_id>', methods=['POST'])
def integrar_order(order_id):
    if order_id in PEDIDOS_PENDENTES:
        PEDIDOS_PENDENTES.pop(order_id)
        print(f"Pedido {order_id} removido da fila/considerado integrado.")
        return jsonify({"success": True})
    return jsonify({"error": "Pedido não encontrado"}), 404

if __name__ == '__main__':
    app.run(debug=True)
