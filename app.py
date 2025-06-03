from flask import Flask, request, jsonify, abort, redirect, Request
from datetime import datetime
import requests
import re
import json
from urllib.parse import urlunparse, urlparse
app = Flask(name)
# ----------- CONFIGURAÇÃO ANTI-GZIP -----------
class NoGzipRequest(Request):
    @property
    def accept_encodings(self):
        return []
app.request_class = NoGzipRequest
@app.beforerequest
def disablegzipproxy():
    if 'HTTPACCEPTENCODING' in request.environ:
        request.environ['HTTPACCEPT_ENCODING'] = ''
@app.afterrequest
def ensureno_compression(response):
    response.headers.pop('Content-Encoding', None)
    response.headers.pop('Vary', None)
if response.content_type and response.content_type.startswith('application/json'):
    response.headers['Content-Type'] = 'application/json; charset=utf-8'

# CORS
response.headers.add('Access-Control-Allow-Origin', '*')
response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,XApiKey')
response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')

return response

@app.beforerequest
def canonicalizeurl():
    path = request.path
    if '//' in path:
        canonicalpath = re.sub(r'/+', '/', path)
        parsedurl = urlparse(request.url)
        canonicalurl = urlunparse(parsedurl.replace(path=canonicalpath))
        return redirect(canonical_url, code=308)
----------- CONFIGURAÇÕES -----------
CARDAPIOWEBBASE = 'https://integracao.cardapioweb.com/api/partner/v1'
CARDAPIOWEBTOKEN = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5'
CARDAPIOWEBMERCHANT = '14104'
CONSUMERAPITOKEN = 'pklive_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy'
Armazenamento em memória
PEDIDOSPENDENTES = {}
PEDIDOSPROCESSADOS = {}
def agora():
    return datetime.utcnow().isoformat() + "Z"
def removenulls(obj):
    if isinstance(obj, dict):
        return {k: removenulls(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [remove_nulls(i) for i in obj if i is not None]
    return obj
def verify_token(req):
    token1 = req.headers.get("XApiKey")
    token2 = req.headers.get("Authorization")
if token1 == CONSUMER_API_TOKEN:
    return True

if token2:
    if token2.startswith("Bearer "):
        token = token2.replace("Bearer ", "")
    else:
        token = token2.split()[-1] if token2.split() else ""
    if token == CONSUMER_API_TOKEN:
        return True

print(f"[AUTH] Token inválido - XApiKey: {token1}, Auth: {token2}")
return False

def transformorderdata(order):
    """Transforma dados do CardapioWeb para formato Consumer"""
    customer = order.get("customer", {})
# Normalizar telefone
phone = customer.get("phone", "")
if isinstance(phone, str):
    customer["phone"] = {"number": phone}
elif isinstance(phone, dict):
    customer["phone"] = phone
else:
    customer["phone"] = {"number": str(phone) if phone else ""}

def fix_option(opt):
    return {
        "optionId": opt.get("option_id", opt.get("optionId")),
        "externalCode": opt.get("external_code", opt.get("externalCode")),
        "name": opt.get("name", ""),
        "optionGroupId": opt.get("option_group_id", opt.get("optionGroupId")),
        "optionGroupName": opt.get("option_group_name", opt.get("optionGroupName")),
        "quantity": int(opt.get("quantity", 1)),
        "unitPrice": float(opt.get("unit_price", opt.get("unitPrice", 0))),
        "optionGroupTotalSelectedOptions": opt.get("option_group_total_selected_options", opt.get("optionGroupTotalSelectedOptions")),
    }

def fix_item(item):
    return {
        "id": str(item.get("item_id", item.get("id", ""))),
        "externalCode": item.get("external_code", item.get("externalCode")),
        "name": item.get("name", ""),
        "quantity": int(item.get("quantity", 1)),
        "unitPrice": float(item.get("unit_price", item.get("unitPrice", 0))),
        "totalPrice": float(item.get("total_price", item.get("totalPrice", 0))),
        "observations": item.get("observation", item.get("observations", "")),
        "options": [fix_option(opt) for opt in item.get("options", [])]
    }

items = [fix_item(i) for i in order.get("items", [])]

delivery = order.get("delivery", {})
address = delivery.get("deliveryAddress", order.get("delivery_address", {}))

delivery_fixed = {
    "deliveredBy": delivery.get("deliveredBy", order.get("delivered_by", "")),
    "deliveryDateTime": delivery.get("deliveryDateTime", order.get("created_at", agora())),
    "mode": delivery.get("mode", order.get("delivery_mode", "")),
    "pickupCode": delivery.get("pickupCode"),
    "deliveryAddress": {
        "country": address.get("country", "Brasil"),
        "state": address.get("state", ""),
        "city": address.get("city", ""),
        "postalCode": address.get("postalCode", address.get("postal_code", "")),
        "streetName": address.get("streetName", address.get("street", "")),
        "streetNumber": address.get("streetNumber", address.get("number", "")),
        "neighborhood": address.get("neighborhood", ""),
        "complement": address.get("complement", ""),
        "reference": address.get("reference", "")
    }
}

order_id = str(order.get("id"))

base = {
    "orderId": order_id,
    "id": order_id,
    "displayId": str(order.get("display_id", order.get("id", ""))),
    "orderType": order.get("order_type", "DELIVERY").upper(),
    "salesChannel": order.get("sales_channel", "CARDAPIOWEB").upper(),
    "orderTiming": order.get("order_timing", "IMMEDIATE").upper(),
    "createdAt": order.get("created_at", agora()),
    "customer": customer,
    "delivery": delivery_fixed,
    "items": items,
    "merchant": {
        "id": str(order.get("merchant_id", CARDAPIOWEB_MERCHANT)),
        "name": order.get("merchant_name", "Restaurante")
    },
    "total": float(order.get("total", 0.0)),
    "payments": order.get("payments", []),
    "status": "NEW",
    "fullCode": "PLACED",
    "code": "PLC"
}

return remove_nulls(base)

# ----------- ROTAS -----------
@app.route('/', methods=['GET'])
def healthcheck():
    return jsonify({
        "status": "OK",
        "service": "Consumer-CardapioWeb API Bridge",
        "timestamp": agora(),
        "version": "2.0.0",
        "pendentes": len(PEDIDOSPENDENTES),
        "processados": len(PEDIDOS_PROCESSADOS)
    })
@app.route('/webhook/orders', methods=['POST'])
@app.route('/webhook/cardapioweb', methods=['POST'])
def webhookorders():
    try:
        event = request.getjson()
        if not event:
            return jsonify({"error": "Payload vazio"}), 400
    print(f"[WEBHOOK] Recebido: {json.dumps(event, indent=2)}")

    order_id = event.get("id") or event.get("order_id")
    if not order_id:
        print(f"[ERRO] Sem order_id no payload: {event}")
        return jsonify({"error": "order_id obrigatório"}), 400

    # Se é notificação simples do CardapioWeb, buscar detalhes
    if "order_id" in event and len(event.keys()) &lt;= 6:
        url = f"{CARDAPIOWEB_BASE}/orders/{order_id}"
        headers = {"X-API-KEY": CARDAPIOWEB_TOKEN, "Content-Type": "application/json"}
        params = {"merchant_id": CARDAPIOWEB_MERCHANT}

        print(f"[WEBHOOK] Buscando detalhes em: {url}")

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            order = resp.json()
            print(f"[WEBHOOK] Detalhes obtidos: {json.dumps(order, indent=2)}")
        except requests.exceptions.RequestException as e:
            print(f"[ERRO] Falha ao buscar detalhes: {e}")
            return jsonify({"error": f"Erro ao buscar detalhes: {str(e)}"}), 502
    else:
        order = event

    # Transformar e armazenar
    pedido_transformado = transform_order_data(order)
    PEDIDOS_PENDENTES[str(order_id)] = pedido_transformado

    print(f"[SUCESSO] Pedido {order_id} armazenado. Total pendentes: {len(PEDIDOS_PENDENTES)}")

    return jsonify({
        "success": True, 
        "orderId": order_id,
        "message": "Pedido recebido e processado"
    })

except Exception as e:
    print(f"[ERRO] Erro no webhook: {str(e)}")
    return jsonify({"error": f"Erro interno: {str(e)}"}), 500

@app.route('/api/parceiro/polling', methods=['GET'])
def polling():
    if not verify_token(request):
        return jsonify({"error": "Token inválido"}), 401
try:
    pedidos = []
    for order_id, pedido in PEDIDOS_PENDENTES.items():
        pedidos.append({
            "id": pedido.get("id", order_id),
            "orderId": pedido.get("orderId", order_id),
            "createdAt": pedido.get("createdAt", agora()),
            "fullCode": pedido.get("fullCode", "PLACED"),
            "code": pedido.get("code", "PLC")
        })

    print(f"[POLLING] Retornando {len(pedidos)} pedidos")

    return jsonify({
        "items": pedidos,
        "statusCode": 0,
        "reasonPhrase": None
    })

except Exception as e:
    print(f"[ERRO] Erro no polling: {str(e)}")
    return jsonify({"error": "Erro interno"}), 500

@app.route('/api/parceiro/orders/<orderid>', methods=['GET'])
def getorder(orderid):
    if not verifytoken(request):
        return jsonify({"error": "Token inválido"}), 401
try:
    pedido = PEDIDOS_PENDENTES.get(str(order_id))
    if not pedido:
        pedido = PEDIDOS_PROCESSADOS.get(str(order_id))

    if not pedido:
        return jsonify({"error": "Pedido não encontrado"}), 404

    print(f"[GET_ORDER] Retornando pedido {order_id}")

    # Mover para processados
    if str(order_id) in PEDIDOS_PENDENTES:
        PEDIDOS_PROCESSADOS[str(order_id)] = PEDIDOS_PENDENTES.pop(str(order_id))
        print(f"[INFO] Pedido {order_id} movido para processados")

    return jsonify(pedido)

except Exception as e:
    print(f"[ERRO] Erro ao buscar pedido: {str(e)}")
    return jsonify({"error": "Erro interno"}), 500

@app.route('/api/parceiro/orders/<orderid>/status', methods=['POST'])
def updatestatus(orderid):
    if not verifytoken(request):
        return jsonify({"error": "Token inválido"}), 401
try:
    data = request.get_json()
    if not data:
        return jsonify({"error": "Payload obrigatório"}), 400

    status = data.get("status")
    if not status:
        return jsonify({"error": "Campo 'status' obrigatório"}), 400

    # Buscar pedido
    pedido = PEDIDOS_PENDENTES.get(str(order_id)) or PEDIDOS_PROCESSADOS.get(str(order_id))
    if not pedido:
        return jsonify({"error": "Pedido não encontrado"}), 404

    # Atualizar status
    pedido["status"] = status
    pedido["fullCode"] = data.get("fullCode", status)
    pedido["code"] = data.get("code", status[:3].upper())
    pedido["updatedAt"] = agora()

    # Salvar atualização
    if str(order_id) in PEDIDOS_PENDENTES:
        PEDIDOS_PENDENTES[str(order_id)] = pedido
    else:
        PEDIDOS_PROCESSADOS[str(order_id)] = pedido

    print(f"[STATUS] Pedido {order_id} atualizado para: {status}")

    return jsonify({
        "success": True,
        "orderId": order_id,
        "status": status,
        "updatedAt": pedido["updatedAt"]
    })

except Exception as e:
    print(f"[ERRO] Erro ao atualizar status: {str(e)}")
    return jsonify({"error": "Erro interno"}), 500

Endpoints de debug
@app.route('/api/debug/orders', methods=['GET'])
def debugorders():
    return jsonify({
        "pendentes": len(PEDIDOSPENDENTES),
        "processados": len(PEDIDOSPROCESSADOS),
        "pedidospendentes": list(PEDIDOSPENDENTES.keys()),
        "pedidosprocessados": list(PEDIDOS_PROCESSADOS.keys()),
        "timestamp": agora()
    })
@app.route('/api/debug/clear', methods=['POST'])
def clearorders():
    global PEDIDOSPENDENTES, PEDIDOSPROCESSADOS
    PEDIDOSPENDENTES.clear()
    PEDIDOS_PROCESSADOS.clear()
    return jsonify({"success": True, "message": "Pedidos limpos"})
CORS Options
@app.route('/api/parceiro/polling', methods=['OPTIONS'])
@app.route('/api/parceiro/orders/<orderid>', methods=['OPTIONS'])
@app.route('/api/parceiro/orders/<orderid>/status', methods=['OPTIONS'])
def handleoptions(orderid=None):
    return '', 200
Error handlers
@app.errorhandler(401)
def unauthorized(error):
    return jsonify({"error": "Token inválido", "code": 401}), 401
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint não encontrado", "code": 404}), 404
@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Erro interno do servidor", "code": 500}), 500
if name == 'main':
    print("[INICIO] Consumer-CardapioWeb API Bridge v2.0.0")
    print(f"[INFO] Timestamp de inicialização: {agora()}")
    app.run(debug=False, host='0.0.0.0', port=8080)
