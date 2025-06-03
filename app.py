from flask import Flask, request, jsonify, abort, redirect, Request
from datetime import datetime
import requests
import re
from urllib.parse import urlunparse, urlparse
app = Flask(name)
----------- ESSENCIAL: Garante que toda resposta seja sem gzip -----------
class NoGzipRequest(Request):
    @property
    def accept_encodings(self):
        # Sempre retorna vazio, não aceitando gzip
        return []
app.request_class = NoGzipRequest
@app.beforerequest
def disablegzipproxy():
    # Remove qualquer Accept-Encoding para proxies/balancer não devolverem gzip
    if 'HTTPACCEPTENCODING' in request.environ:
        request.environ['HTTPACCEPT_ENCODING'] = ''
@app.afterrequest
def ensureno_compression(response):
    # Remove qualquer header de compressão
    response.headers.pop('Content-Encoding', None)
    response.headers.pop('Vary', None)
# Força content-type para JSON
if response.content_type and response.content_type.startswith('application/json'):
    response.headers['Content-Type'] = 'application/json; charset=utf-8'

# CORS headers (se necessário)
response.headers.add('Access-Control-Allow-Origin', '*')
response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,XApiKey')
response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')

return response

@app.beforerequest
def canonicalizeurlremovedoubleslash():
    path = request.path
    if '//' in path:
        canonicalpath = re.sub(r'/+', '/', path)
        parsedurl = urlparse(request.url)
        canonicalurl = urlunparse(parsedurl.replace(path=canonicalpath))
        print(f"[INFO] Redirecionando barra dupla: {request.url} --> {canonicalurl}")
        return redirect(canonical_url, code=308)
-------------------------------------------------------------------------
Configurações
CARDAPIOWEBBASE = 'https://integracao.cardapioweb.com/api/partner/v1'
CARDAPIOWEBTOKEN = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5'
CARDAPIOWEBMERCHANT = '14104'
CONSUMERAPITOKEN = 'pklive_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy'
Armazenamento em memória (em produção usar Redis ou banco de dados)
PEDIDOSPENDENTES = {}
PEDIDOSPROCESSADOS = {}
def agora():
    return datetime.utcnow().isoformat() + "Z"
def removenulls(obj):
    """Remove valores nulos/None do objeto"""
    if isinstance(obj, dict):
        return {k: removenulls(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [remove_nulls(i) for i in obj]
    else:
        return obj
def verify_token(request):
    """Verifica se o token de autenticação está correto"""
    token1 = request.headers.get("XApiKey")
    token2 = request.headers.get("Authorization")
if token1 == CONSUMER_API_TOKEN:
    return True

if token2:
    # Suporte para Bearer token
    if token2.startswith("Bearer "):
        token = token2.replace("Bearer ", "")
    else:
        token = token2.split()[-1]

    if token == CONSUMER_API_TOKEN:
        return True

print(f"[AUTH] Token inválido recebido! XApiKey={token1} Authorization={token2}")
return False

def transformorderdata(order):
    """Transforma dados do pedido para o formato esperado pelo Consumer"""
    customer = order.get("customer") or {}
# Normalizar telefone
phone = customer.get("phone", "")
if isinstance(phone, str):
    customer["phone"] = {"number": phone}
elif isinstance(phone, dict):
    customer["phone"] = phone
else:
    customer["phone"] = {"number": str(phone) if phone else ""}

def fix_option(opt):
    """Normaliza opções do item"""
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
    """Normaliza itens do pedido"""
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

# Processar itens
items = [fix_item(i) for i in order.get("items", [])]

# Processar delivery
delivery = order.get("delivery", {})
address = delivery.get("deliveryAddress") or order.get("delivery_address") or {}

delivery_fixed = {
    "deliveredBy": delivery.get("deliveredBy", order.get("delivered_by", "")),
    "deliveryDateTime": delivery.get("deliveryDateTime", order.get("created_at", agora())),
    "mode": delivery.get("mode", order.get("delivered_by", "")),
    "pickupCode": delivery.get("pickupCode", None),
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

# Montar objeto final
order_id = str(order.get("id"))

base = {
    "orderId": order_id,
    "id": order_id,
    "displayId": str(order.get("display_id", "")),
    "orderType": order.get("order_type", "").upper(),
    "salesChannel": order.get("sales_channel", "").upper(),
    "orderTiming": order.get("order_timing", "").upper(),
    "createdAt": order.get("created_at", agora()),
    "customer": customer,
    "delivery": delivery_fixed,
    "items": items,
    "merchant": {
        "id": str(order.get("merchant_id", CARDAPIOWEB_MERCHANT)),
        "name": "Seu Restaurante"
    },
    "total": float(order.get("total", 0.0)),
    "payments": order.get("payments", []),
    "status": "NEW",
    "fullCode": "PLACED",
    "code": "PLC"
}

return remove_nulls(base)

===================== ROTAS =====================
@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "OK",
        "service": "Consumer-CardapioWeb API",
        "timestamp": agora(),
        "version": "1.0.0"
    })
@app.route('/webhook/orders', methods=['POST'])
@app.route('/webhook/cardapioweb', methods=['POST'])
def webhookorders():
    """Recebe webhooks de pedidos do CardapioWeb"""
    try:
        event = request.getjson()
        print(f"\n[WEBHOOK] Recebido: {event} (type={type(event)})")
    if not event:
        return jsonify({"error": "Payload vazio"}), 400

    order_id = event.get("id") or event.get("order_id")
    if not order_id:
        print("[ERRO] Payload sem id/order_id:", event)
        return jsonify({"error": "Payload inesperado, sem id/order_id", "raw": event}), 400

    # Caso payload típico do CardapioWeb, busca detalhes via API
    if "order_id" in event and len(event.keys()) &lt;= 6:
        url = f"{CARDAPIOWEB_BASE}/orders/{order_id}"
        headers = {"X-API-KEY": CARDAPIOWEB_TOKEN, "Content-Type": "application/json"}
        params = {"merchant_id": CARDAPIOWEB_MERCHANT}

        print(f"[WEBHOOK] CardapioWeb: Buscando detalhes do pedido em {url} ...")

        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"[ERRO] Falha ao buscar detalhes do pedido {order_id} - Status: {resp.status_code}")
            return jsonify({"error": "Falha ao obter detalhes", "status": resp.status_code}), 500

        order = resp.json()
        print(f"[WEBHOOK] Detalhes recebidos do CardapioWeb: {order}")
    else:
        order = event

    # Transformar e armazenar pedido
    pedido_transformado = transform_order_data(order)
    PEDIDOS_PENDENTES[str(order_id)] = pedido_transformado

    print(f"[ARMAZENADO] Pedido {order_id} armazenado em PEDIDOS_PENDENTES")
    print(f"[INFO] Total de pedidos pendentes: {len(PEDIDOS_PENDENTES)}")

    return jsonify({"success": True, "orderId": order_id})

except requests.exceptions.Timeout:
    return jsonify({"error": "Timeout ao buscar detalhes do pedido"}), 504
except requests.exceptions.RequestException as e:
    print(f"[ERRO] Erro na requisição: {str(e)}")
    return jsonify({"error": "Erro ao comunicar com CardapioWeb"}), 502
except Exception as e:
    print(f"[ERRO] Erro no webhook: {str(e)}")
    return jsonify({"error": "Erro interno do servidor"}), 500

@app.route('/api/parceiro/polling', methods=['GET'])
def polling():
    """Endpoint de polling para o Consumer buscar novos pedidos"""
    if not verify_token(request):
        print(f"[UNAUTHORIZED] Polling sem token correto.")
        return abort(401)
try:
    pedidos = []
    for order_id, p in PEDIDOS_PENDENTES.items():
        pedidos.append({
            "id": str(p.get("id")),
            "orderId": str(p.get("orderId", p.get("id"))),
            "createdAt": p.get("createdAt", agora()),
            "fullCode": p.get("fullCode", "PLACED"),
            "code": p.get("code", "PLC"),
        })

    print(f"[POLLING] Retornando {len(pedidos)} pedidos pendentes")

    response_data = {
        "items": pedidos,
        "statusCode": 0,
        "reasonPhrase": None
    }

    response = jsonify(response_data)
    response.headers['Content-Type'] = 'application/json; charset=utf-8'

    return response

except Exception as e:
    print(f"[ERRO] Erro no polling: {str(e)}")
    return jsonify({"error": "Erro interno do servidor"}), 500

@app.route('/api/parceiro/orders/<orderid>', methods=['GET'])
def getorder(orderid):
    """Busca detalhes de um pedido específico"""
    if not verifytoken(request):
        return abort(401)
try:
    # Primeiro verifica se está em pendentes
    pedido = PEDIDOS_PENDENTES.get(str(order_id))

    # Se não encontrou, verifica nos processados
    if not pedido:
        pedido = PEDIDOS_PROCESSADOS.get(str(order_id))

    if not pedido:
        print(f"[INFO] Pedido {order_id} não encontrado")
        return jsonify({"error": "Pedido não encontrado"}), 404

    print(f"[GET_ORDER] Retornando detalhes do pedido {order_id}")

    response = jsonify(pedido)
    response.headers['Content-Type'] = 'application/json; charset=utf-8'

    # Move pedido para processados após ser consumido
    if str(order_id) in PEDIDOS_PENDENTES:
        PEDIDOS_PROCESSADOS[str(order_id)] = PEDIDOS_PENDENTES.pop(str(order_id))
        print(f"[INFO] Pedido {order_id} movido para processados")

    return response

except Exception as e:
    print(f"[ERRO] Erro ao buscar pedido {order_id}: {str(e)}")
    return jsonify({"error": "Erro interno do servidor"}), 500

@app.route('/api/parceiro/orders/<orderid>/status', methods=['POST'])
def updateorderstatus(orderid):
    """Atualiza status de um pedido"""
    if not verify_token(request):
        return abort(401)
try:
    data = request.get_json()
    if not data:
        return jsonify({"error": "Payload vazio"}), 400

    new_status = data.get("status")
    if not new_status:
        return jsonify({"error": "Status é obrigatório"}), 400

    # Busca o pedido
    pedido = PEDIDOS_PENDENTES.get(str(order_id)) or PEDIDOS_PROCESSADOS.get(str(order_id))

    if not pedido:
        return jsonify({"error": "Pedido não encontrado"}), 404

    # Atualiza status
    pedido["status"] = new_status
    pedido["fullCode"] = data.get("fullCode", new_status)
    pedido["code"] = data.get("code", new_status[:3].upper())
    pedido["updatedAt"] = agora()

    # Salva a atualização
    if str(order_id) in PEDIDOS_PENDENTES:
        PEDIDOS_PENDENTES[str(order_id)] = pedido
    else:
        PEDIDOS_PROCESSADOS[str(order_id)] = pedido

    print(f"[STATUS_UPDATE] Pedido {order_id} atualizado para status: {new_status}")

    # Aqui você pode adicionar lógica para notificar o CardapioWeb sobre a mudança de status
    # notify_cardapioweb_status_change(order_id, new_status)

    return jsonify({"success": True, "orderId": order_id, "status": new_status})

except Exception as e:
    print(f"[ERRO] Erro ao atualizar status do pedido {order_id}: {str(e)}")
    return jsonify({"error": "Erro interno do servidor"}), 500

@app.route('/api/debug/orders', methods=['GET'])
def debugorders():
    """Endpoint para debug - lista todos os pedidos (remover em produção)"""
    return jsonify({
        "pendentes": len(PEDIDOSPENDENTES),
        "processados": len(PEDIDOSPROCESSADOS),
        "pedidospendentes": list(PEDIDOSPENDENTES.keys()),
        "pedidosprocessados": list(PEDIDOS_PROCESSADOS.keys())
    })
Suporte para OPTIONS requests (CORS)
@app.route('/api/parceiro/polling', methods=['OPTIONS'])
@app.route('/api/parceiro/orders/<orderid>', methods=['OPTIONS'])
@app.route('/api/parceiro/orders/<orderid>/status', methods=['OPTIONS'])
def handleoptions(orderid=None):
    return '', 200
Error handlers
@app.errorhandler(401)
def unauthorized(error):
    return jsonify({"error": "Token de autenticação inválido"}), 401
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint não encontrado"}), 404
@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Erro interno do servidor"}), 500
if name == 'main':
    print("[INFO] Iniciando Consumer-CardapioWeb API…")
    print(f"[INFO] Timestamp: {agora()}")
    app.run(debug=False, host='0.0.0.0', port=8080)
