from flask import Flask, request, jsonify, abort, redirect
from datetime import datetime, timezone
import requests
import re
import json
from urllib.parse import urlunparse, urlparse

app = Flask(__name__)

# ----------- CONFIGURAÇÃO ANTI-GZIP -----------
# Embora o Flask/Werkzeug geralmente lide com a descompressão de requests com gzip,
# esta classe e o before_request visam explicitamente sinalizar que não aceitamos encodings.
# A parte mais crucial é a manipulação do response no after_request.

class NoGzipRequest(request.__class__): # Herda da classe de request original
    @property
    def accept_encodings(self):
        return "" # Remove 'gzip' e outros da lista de encodings aceitos

app.request_class = NoGzipRequest

@app.before_request
def disable_gzip_proxy():
    # Alguns proxies podem adicionar 'Accept-Encoding' de qualquer maneira.
    # Esta é uma tentativa adicional de influenciar isso.
    if 'HTTP_ACCEPT_ENCODING' in request.environ:
        # Modificar request.environ pode não ser universalmente eficaz dependendo do servidor WSGI
        # mas é uma tentativa válida.
        pass # Deixado como no original, mas sua eficácia pode variar.
             # A abordagem principal é no after_request para a resposta.

@app.after_request
def after_request_handler(response):
    # Remover headers de encoding da resposta para evitar compressão
    response.headers.pop('Content-Encoding', None)
    response.headers.pop('Vary', None) # Vary pode estar relacionado a Content-Encoding

    # Garantir charset UTF-8 para JSON
    if response.content_type and response.content_type.startswith('application/json'):
        response.headers['Content-Type'] = 'application/json; charset=utf-8'

    # Headers CORS
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Api-Key') # X-Api-Key adicionado para consistência
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')

    return response

@app.before_request
def canonicalize_url():
    path = request.path
    if '//' in path:
        canonical_path = re.sub(r'/+', '/', path)
        parsed_url = urlparse(request.url)
        # Cuidado: request.url pode ser http se o proxy SSL terminar antes.
        # Idealmente, o esquema e netloc devem vir de uma fonte confiável ou configuração.
        canonical_url = urlunparse(parsed_url._replace(path=canonical_path))
        return redirect(canonical_url, code=308)

# ----------- CONFIGURAÇÕES -----------
CARDAPIOWEB_BASE_URL = 'https://integracao.cardapioweb.com/api/partner/v1'
CARDAPIOWEB_API_KEY = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5' # Seu token do CardapioWeb
CARDAPIOWEB_MERCHANT_ID = '14104' # Seu ID de lojista do CardapioWeb
CONSUMER_API_TOKEN = 'pklive_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy' # Seu token da API Consumer

# Armazenamento em memória (Para produção, considere um banco de dados ou Redis)
PEDIDOS_PENDENTES = {}
PEDIDOS_PROCESSADOS = {}

def agora_iso():
    """Retorna o timestamp atual em formato ISO UTC com 'Z'."""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def _remove_null_values_recursive(obj):
    """Helper recursivo para remover chaves com valores None."""
    if isinstance(obj, dict):
        return {k: _remove_null_values_recursive(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [_remove_null_values_recursive(i) for i in obj if i is not None]
    return obj

def remove_null_values(obj):
    """Remove chaves com valores None de um objeto (dicionário ou lista)."""
    return _remove_null_values_recursive(obj)

def verify_token(req):
    """Verifica o token de autenticação na requisição."""
    token_xapi = req.headers.get("X-Api-Key") # Padronizado para X-Api-Key
    token_auth = req.headers.get("Authorization")

    if token_xapi and token_xapi == CONSUMER_API_TOKEN:
        return True

    if token_auth:
        if token_auth.startswith("Bearer "):
            token = token_auth.replace("Bearer ", "")
        else:
            # Considera o caso de apenas o token ser passado, embora menos comum para "Bearer"
            token = token_auth.split()[-1] if token_auth.split() else ""
        
        if token == CONSUMER_API_TOKEN:
            return True

    print(f"[{agora_iso()}] [AUTH_FAIL] Token inválido - X-Api-Key: {token_xapi}, Authorization: {token_auth}")
    return False

def transform_order_data(order_payload):
    """Transforma os dados do pedido do formato CardapioWeb para o formato Consumer."""
    customer_data = order_payload.get("customer", {})
    phone_info = customer_data.get("phone", "")

    if isinstance(phone_info, str):
        customer_data["phone"] = {"number": phone_info}
    elif isinstance(phone_info, dict):
        # Assume que já está no formato {"number": "...", ...} ou similar
        customer_data["phone"] = phone_info
    else:
        customer_data["phone"] = {"number": str(phone_info) if phone_info is not None else ""}

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

    def fix_item(item_data):
        return {
            "id": str(item_data.get("item_id", item_data.get("id", ""))), # ID do item
            "externalCode": item_data.get("external_code", item_data.get("externalCode")),
            "name": item_data.get("name", ""),
            "quantity": int(item_data.get("quantity", 1)),
            "unitPrice": float(item_data.get("unit_price", item_data.get("unitPrice", 0))),
            "totalPrice": float(item_data.get("total_price", item_data.get("totalPrice", 0))),
            "observations": item_data.get("observation", item_data.get("observations", "")),
            "options": [fix_option(opt) for opt in item_data.get("options", [])],
        }

    items_list = [fix_item(i) for i in order_payload.get("items", [])]
    delivery_data = order_payload.get("delivery", {})
    address_data = delivery_data.get("deliveryAddress", order_payload.get("delivery_address", {}))

    delivery_fixed = {
        "deliveredBy": delivery_data.get("deliveredBy", order_payload.get("delivered_by", "")),
        "deliveryDateTime": delivery_data.get("deliveryDateTime", order_payload.get("created_at", agora_iso())),
        "mode": delivery_data.get("mode", order_payload.get("delivery_mode", "")),
        "pickupCode": delivery_data.get("pickupCode"), # Pode ser None
        "deliveryAddress": {
            "country": address_data.get("country", "Brasil"),
            "state": address_data.get("state", ""),
            "city": address_data.get("city", ""),
            "postalCode": address_data.get("postalCode", address_data.get("postal_code", "")),
            "streetName": address_data.get("streetName", address_data.get("street", "")),
            "streetNumber": address_data.get("streetNumber", address_data.get("number", "")),
            "neighborhood": address_data.get("neighborhood", ""),
            "complement": address_data.get("complement", ""),
            "reference": address_data.get("reference", "")
        }
    }

    cardapio_web_order_id = str(order_payload.get("id")) # ID principal do pedido no CardapioWeb

    transformed_base = {
        "id": cardapio_web_order_id,  # Usado como ID principal para o Consumer
        "orderId": cardapio_web_order_id, # ID original do CardapioWeb, para referência
        "displayId": str(order_payload.get("display_id", order_payload.get("id", ""))),
        "orderType": order_payload.get("order_type", "DELIVERY").upper(),
        "salesChannel": order_payload.get("sales_channel", "CARDAPIOWEB").upper(),
        "orderTiming": order_payload.get("order_timing", "IMMEDIATE").upper(),
        "createdAt": order_payload.get("created_at", agora_iso()),
        "customer": customer_data,
        "delivery": delivery_fixed,
        "items": items_list,
        "merchant": {
            "id": str(order_payload.get("merchant_id", CARDAPIOWEB_MERCHANT_ID)),
            "name": order_payload.get("merchant_name", "Restaurante Padrão") # Nome padrão se não vier
        },
        "total": float(order_payload.get("total", 0.0)),
        "payments": order_payload.get("payments", []), # Pode precisar de transformação detalhada
        "status": "NEW", # Status inicial ao ser recebido pela bridge
        "fullCode": "PLACED", # Código completo inicial
        "code": "PLC" # Código curto inicial
    }
    return remove_null_values(transformed_base)

# ----------- ROTAS -----------
@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        "status": "OK",
        "service": "Consumer-CardapioWeb API Bridge",
        "timestamp": agora_iso(),
        "version": "2.1.0", # Versão atualizada
        "pedidos_pendentes_count": len(PEDIDOS_PENDENTES),
        "pedidos_processados_count": len(PEDIDOS_PROCESSADOS)
    })

@app.route('/webhook/orders', methods=['POST'])
@app.route('/webhook/cardapioweb', methods=['POST']) # Rota alternativa
def webhook_orders():
    event_data = request.get_json(silent=True)
    if not event_data:
        print(f"[{agora_iso()}] [WEBHOOK_ERROR] Payload JSON vazio ou malformado.")
        return jsonify({"error": "Payload JSON inválido ou ausente."}), 400
    
    print(f"[{agora_iso()}] [WEBHOOK_RECEIVED] Payload: {json.dumps(event_data, ensure_ascii=False)}")

    # O ID do pedido no CardapioWeb pode vir em "id" ou "order_id" no webhook simplificado
    order_id_from_event = str(event_data.get("id") or event_data.get("order_id"))
    if not order_id_from_event or order_id_from_event == "None":
        print(f"[{agora_iso()}] [WEBHOOK_ERROR] Campo 'id' ou 'order_id' ausente no payload: {event_data}")
        return jsonify({"error": "'id' ou 'order_id' do pedido é obrigatório no payload."}), 400

    # Verifica se é uma notificação simplificada do CardapioWeb que requer busca de detalhes.
    # Uma notificação simplificada pode ter poucas chaves (ex: id, status, merchant_id).
    # Ajuste esta lógica se o CardapioWeb tiver um campo específico para indicar isso.
    is_simple_notification = "order_id" in event_data and len(event_data.keys()) <= 6 

    order_details = event_data
    if is_simple_notification:
        url = f"{CARDAPIOWEB_BASE_URL}/orders/{order_id_from_event}"
        headers = {"X-API-KEY": CARDAPIOWEB_API_KEY, "Content-Type": "application/json"}
        params = {"merchant_id": CARDAPIOWEB_MERCHANT_ID}
        print(f"[{agora_iso()}] [WEBHOOK_FETCH] Buscando detalhes do pedido {order_id_from_event} em: {url}")
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status() # Lança exceção para status HTTP 4xx/5xx
            order_details = response.json()
            print(f"[{agora_iso()}] [WEBHOOK_FETCH_SUCCESS] Detalhes obtidos para {order_id_from_event}: {json.dumps(order_details, ensure_ascii=False)}")
        except requests.exceptions.RequestException as e:
            print(f"[{agora_iso()}] [WEBHOOK_FETCH_ERROR] Falha ao buscar detalhes do pedido {order_id_from_event}: {e}")
            return jsonify({"error": f"Erro ao buscar detalhes do pedido no CardapioWeb: {str(e)}"}), 502 # Bad Gateway

    try:
        pedido_transformado = transform_order_data(order_details)
        # Usa o ID do CardapioWeb como chave nos dicionários internos
        internal_order_id = pedido_transformado["id"] 
        PEDIDOS_PENDENTES[internal_order_id] = pedido_transformado
        print(f"[{agora_iso()}] [WEBHOOK_SUCCESS] Pedido {internal_order_id} armazenado. Total pendentes: {len(PEDIDOS_PENDENTES)}")
        return jsonify({
            "success": True,
            "orderId": internal_order_id,
            "message": "Pedido recebido e agendado para processamento."
        }), 200 # Ou 202 Accepted se o processamento for demorado
    except Exception as e:
        print(f"[{agora_iso()}] [WEBHOOK_TRANSFORM_ERROR] Erro ao transformar ou armazenar pedido {order_id_from_event}: {e}")
        # Considerar loggar o traceback completo aqui
        return jsonify({"error": f"Erro interno ao processar o pedido: {str(e)}"}), 500

@app.route('/api/parceiro/polling', methods=['GET'])
def polling_orders():
    if not verify_token(request):
        return jsonify({"error": "Token de autenticação inválido ou ausente."}), 401
    
    try:
        pedidos_para_consumer = []
        # Iterar sobre uma cópia das chaves se houver modificação concorrente (raro em Flask single worker)
        for order_key in list(PEDIDOS_PENDENTES.keys()): 
            pedido = PEDIDOS_PENDENTES.get(order_key)
            if pedido:
                pedidos_para_consumer.append({
                    "id": pedido.get("id"), # Este é o ID que o Consumer usará (originalmente do CardapioWeb)
                    "orderId": pedido.get("orderId"), # ID original do CardapioWeb, para referência
                    "createdAt": pedido.get("createdAt", agora_iso()),
                    "fullCode": pedido.get("fullCode", "PLACED"),
                    "code": pedido.get("code", "PLC")
                })
        
        print(f"[{agora_iso()}] [POLLING_SUCCESS] Retornando {len(pedidos_para_consumer)} pedidos pendentes.")
        return jsonify({
            "items": pedidos_para_consumer,
            "statusCode": 0, # Conforme exemplo do Consumer
            "reasonPhrase": None # Conforme exemplo do Consumer
        }), 200
    except Exception as e:
        print(f"[{agora_iso()}] [POLLING_ERROR] Erro ao processar polling: {e}")
        return jsonify({"error": "Erro interno ao processar a requisição de polling."}), 500

@app.route('/api/parceiro/orders/<string:order_id>', methods=['GET'])
def get_order_details(order_id):
    if not verify_token(request):
        return jsonify({"error": "Token de autenticação inválido ou ausente."}), 401
    
    try:
        pedido = PEDIDOS_PENDENTES.get(str(order_id))
        if not pedido:
            pedido = PEDIDOS_PROCESSADOS.get(str(order_id))

        if not pedido:
            print(f"[{agora_iso()}] [GET_ORDER_NOT_FOUND] Pedido {order_id} não encontrado.")
            return jsonify({"error": "Pedido não encontrado."}), 404

        print(f"[{agora_iso()}] [GET_ORDER_SUCCESS] Retornando detalhes do pedido {order_id}.")
        
        # Mover para processados se ainda estiver em pendentes
        if str(order_id) in PEDIDOS_PENDENTES:
            PEDIDOS_PROCESSADOS[str(order_id)] = PEDIDOS_PENDENTES.pop(str(order_id))
            print(f"[{agora_iso()}] [GET_ORDER_MOVE] Pedido {order_id} movido de pendentes para processados.")

        return jsonify(pedido), 200
    except Exception as e:
        print(f"[{agora_iso()}] [GET_ORDER_ERROR] Erro ao buscar pedido {order_id}: {e}")
        return jsonify({"error": "Erro interno ao buscar o pedido."}), 500

@app.route('/api/parceiro/orders/<string:order_id>/status', methods=['POST'])
def update_order_status(order_id):
    if not verify_token(request):
        return jsonify({"error": "Token de autenticação inválido ou ausente."}), 401
    
    data = request.get_json(silent=True)
    if not data:
        print(f"[{agora_iso()}] [UPDATE_STATUS_ERROR] Payload JSON inválido ou ausente para pedido {order_id}.")
        return jsonify({"error": "Payload JSON obrigatório."}), 400

    new_status = data.get("status")
    if not new_status:
        print(f"[{agora_iso()}] [UPDATE_STATUS_ERROR] Campo 'status' ausente no payload para pedido {order_id}.")
        return jsonify({"error": "Campo 'status' é obrigatório."}), 400

    try:
        pedido_ref = None
        if str(order_id) in PEDIDOS_PENDENTES:
            pedido_ref = PEDIDOS_PENDENTES[str(order_id)]
        elif str(order_id) in PEDIDOS_PROCESSADOS:
            pedido_ref = PEDIDOS_PROCESSADOS[str(order_id)]

        if not pedido_ref:
            print(f"[{agora_iso()}] [UPDATE_STATUS_NOT_FOUND] Pedido {order_id} não encontrado para atualização de status.")
            return jsonify({"error": "Pedido não encontrado para atualização de status."}), 404

        # Atualizar o status e campos relacionados
        pedido_ref["status"] = new_status
        pedido_ref["fullCode"] = data.get("fullCode", new_status.upper()) # Usa new_status se fullCode não vier
        pedido_ref["code"] = data.get("code", new_status[:3].upper()) # Usa 3 primeiros chars de status se code não vier
        pedido_ref["updatedAt"] = agora_iso()

        # Se estava em pendentes, agora que Consumer interagiu, pode ser movido para processados (opcional, dependendo da lógica desejada)
        # if str(order_id) in PEDIDOS_PENDENTES:
        #     PEDIDOS_PROCESSADOS[str(order_id)] = PEDIDOS_PENDENTES.pop(str(order_id))
        #     print(f"[{agora_iso()}] [UPDATE_STATUS_MOVE] Pedido {order_id} movido para processados após atualização de status.")
        # else:
        #     PEDIDOS_PROCESSADOS[str(order_id)] = pedido_ref # Garante que está em processados

        print(f"[{agora_iso()}] [UPDATE_STATUS_SUCCESS] Status do pedido {order_id} atualizado para: {new_status}.")
        return jsonify({
            "success": True,
            "orderId": order_id,
            "status": pedido_ref["status"],
            "updatedAt": pedido_ref["updatedAt"]
        }), 200
    except Exception as e:
        print(f"[{agora_iso()}] [UPDATE_STATUS_ERROR] Erro ao atualizar status do pedido {order_id}: {e}")
        return jsonify({"error": "Erro interno ao atualizar o status do pedido."}), 500

# TODO: Implementar novo endpoint para receber alterações do PDV vindas do Consumer,
#       conforme mencionado por Yuri. Exemplo:
# @app.route('/api/parceiro/pdv/changes', methods=['POST'])
# def pdv_changes_webhook():
# if not verify_token(request):
# return jsonify({"error": "Token inválido"}), 401
# pdv_update_data = request.get_json()
# Processar pdv_update_data (ex: encaminhar para CardapioWeb se necessário)
# print(f"[{agora_iso()}] [PDV_CHANGES] Recebida alteração do PDV: {pdv_update_data}")
# return jsonify({"success": True, "message": "Alteração do PDV recebida."}), 200

# Endpoints de Debug (opcional, remova ou proteja em produção)
@app.route('/api/debug/orders', methods=['GET'])
def debug_list_orders():
    # Adicionar uma verificação de token simples ou IP para proteger este endpoint se mantido
    return jsonify({
        "pedidos_pendentes_count": len(PEDIDOS_PENDENTES),
        "pedidos_processados_count": len(PEDIDOS_PROCESSADOS),
        "ids_pedidos_pendentes": list(PEDIDOS_PENDENTES.keys()),
        "ids_pedidos_processados": list(PEDIDOS_PROCESSADOS.keys()),
        "timestamp": agora_iso()
    })

@app.route('/api/debug/clear', methods=['POST'])
def debug_clear_orders():
    # Adicionar uma verificação de token simples ou IP para proteger este endpoint se mantido
    global PEDIDOS_PENDENTES, PEDIDOS_PROCESSADOS
    PEDIDOS_PENDENTES.clear()
    PEDIDOS_PROCESSADOS.clear()
    print(f"[{agora_iso()}] [DEBUG_CLEAR] Todos os pedidos em memória foram limpos.")
    return jsonify({"success": True, "message": "Todos os pedidos em memória foram limpos."})

# Tratamento para requisições OPTIONS (CORS preflight)
# O @app.after_request já adiciona os headers globais,
# mas rotas explícitas OPTIONS podem ser necessárias para alguns firewalls/gateways.
# Flask geralmente lida com OPTIONS automaticamente se os headers corretos estiverem no after_request.
# Estas rotas podem ser simplificadas ou removidas se o after_request for suficiente.
@app.route('/api/parceiro/polling', methods=['OPTIONS'])
@app.route('/api/parceiro/orders/<string:order_id>', methods=['OPTIONS'])
@app.route('/api/parceiro/orders/<string:order_id>/status', methods=['OPTIONS'])
def handle_options_requests(order_id=None): # order_id é opcional aqui
    # A resposta para OPTIONS deve ser mínima, com os headers CORS corretos (já feitos no after_request)
    return '', 204 # No Content é uma resposta comum para OPTIONS


# Error handlers (Opcional, Flask tem padrões, mas customizar pode ser útil)
@app.errorhandler(400)
def bad_request_error(error):
    return jsonify({"error": "Requisição inválida.", "details": str(error)}), 400

@app.errorhandler(401)
def unauthorized_error(error):
    return jsonify({"error": "Não autorizado. Token inválido ou ausente.", "details": str(error)}), 401

@app.errorhandler(404)
def not_found_error(error):
    return jsonify({"error": "Recurso não encontrado.", "details": str(error)}), 404

@app.errorhandler(500)
def internal_server_error(error):
    return jsonify({"error": "Erro interno do servidor.", "details": str(error)}), 500


if __name__ == '__main__':
    print(f"[{agora_iso()}] [STARTUP] Iniciando Consumer-CardapioWeb API Bridge v2.1.0")
    # Para deploy, use um servidor WSGI como Gunicorn ou uWSGI.
    # Ex: gunicorn -w 4 -b 0.0.0.0:8080 main:app
    # O debug=True do Flask não é recomendado para produção.
    app.run(debug=False, host='0.0.0.0', port=8080)
