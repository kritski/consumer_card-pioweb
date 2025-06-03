import requests
import re
import json
import traceback # Para logging detalhado de exceções
from datetime import datetime, timezone
from urllib.parse import urlunparse, urlparse
from flask import Flask, request, jsonify, abort, redirect, Request # Importar Request explicitamente

# Inicialização da aplicação Flask
app = Flask(__name__)

# ----------- CONFIGURAÇÃO ANTI-GZIP -----------
# Classe de Request customizada para tentar influenciar o 'Accept-Encoding'
# Esta classe será usada pelo Flask para criar objetos 'request'.
class NoGzipRequest(Request): # Herdar diretamente de flask.Request
    @property
    def accept_encodings(self):
        # Sobrepõe a propriedade para indicar que não aceitamos encodings comprimidos.
        # Retornar uma string vazia.
        return ""

app.request_class = NoGzipRequest

# Este before_request tenta modificar o environ para remover o 'Accept-Encoding'.
# Sua eficácia pode variar dependendo do servidor WSGI e proxies.
@app.before_request
def disable_gzip_in_request_environ():
    # Acessar request.environ aqui é seguro.
    if 'HTTP_ACCEPT_ENCODING' in request.environ:
        # Para sinalizar que o cliente original (ou este proxy) não quer gzip na resposta a ele.
        # No entanto, a manipulação da RESPOSTA no after_request é mais crucial.
        # request.environ['HTTP_ACCEPT_ENCODING'] = '' # Pode ser redundante se NoGzipRequest funcionar como esperado
        pass # Deixado como no original, sua principal função é no after_request

# Este after_request é crucial para garantir que as RESPOSTAS da NOSSA API não sejam comprimidas
# e para adicionar headers CORS.
@app.after_request
def after_request_handler(response):
    # Remover headers de encoding da resposta para evitar compressão pela NOSSA aplicação
    response.headers.pop('Content-Encoding', None)
    response.headers.pop('Vary', None) # Vary pode estar relacionado a Content-Encoding

    # Garantir charset UTF-8 para JSON
    if response.content_type and response.content_type.startswith('application/json'):
        response.headers['Content-Type'] = 'application/json; charset=utf-8'

    # Headers CORS
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Api-Key')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')

    return response

@app.before_request
def canonicalize_url_redirect(): # Nomeado para evitar conflito se 'canonicalize_url' for uma variável global
    path = request.path
    if '//' in path:
        canonical_path = re.sub(r'/+', '/', path)
        parsed_url = urlparse(request.url)
        canonical_url = urlunparse(parsed_url._replace(path=canonical_path))
        return redirect(canonical_url, code=308)

# ----------- CONFIGURAÇÕES GLOBAIS -----------
CARDAPIOWEB_BASE_URL = 'https://integracao.cardapioweb.com/api/partner/v1'
CARDAPIOWEB_API_KEY = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5' # Seu token do CardapioWeb
CARDAPIOWEB_MERCHANT_ID = '14104' # Seu ID de lojista do CardapioWeb
CONSUMER_API_TOKEN = 'pklive_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy' # Seu token da API Consumer

# Armazenamento em memória (Para produção, considere um banco de dados ou Redis)
# A linha abaixo era a causa do SyntaxError. Deve ser um comentário.
# Armazenamento em memória (Para produção, considere um banco de dados ou Redis)
PEDIDOS_PENDENTES = {}
PEDIDOS_PROCESSADOS = {}

# ----------- FUNÇÕES AUXILIARES -----------
def agora_iso():
    """Retorna o timestamp atual em formato ISO UTC com 'Z'."""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def _remove_null_values_recursive(obj_data):
    """Helper recursivo para remover chaves com valores None."""
    if isinstance(obj_data, dict):
        return {k: _remove_null_values_recursive(v) for k, v in obj_data.items() if v is not None}
    elif isinstance(obj_data, list):
        return [_remove_null_values_recursive(i) for i in obj_data if i is not None]
    return obj_data

def remove_null_values(obj_data):
    """Remove chaves com valores None de um objeto (dicionário ou lista)."""
    return _remove_null_values_recursive(obj_data)

def verify_token(current_request):
    """Verifica o token de autenticação na requisição."""
    token_xapi = current_request.headers.get("X-Api-Key")
    token_auth = current_request.headers.get("Authorization")

    if token_xapi and token_xapi == CONSUMER_API_TOKEN:
        return True

    if token_auth:
        scheme, _, token_value = token_auth.partition(' ')
        if scheme.lower() == "bearer" and token_value == CONSUMER_API_TOKEN:
            return True

    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [AUTH_FAIL] Token inválido - X-Api-Key: {token_xapi}, Authorization: {token_auth}")
    return False

def transform_order_data(order_payload):
    """Transforma os dados do pedido do formato CardapioWeb para o formato Consumer."""
    customer_data = order_payload.get("customer", {})
    phone_info = customer_data.get("phone", "")

    if isinstance(phone_info, str):
        customer_data["phone"] = {"number": phone_info}
    elif isinstance(phone_info, dict):
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
            "id": str(item_data.get("item_id", item_data.get("id", ""))),
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
    current_time_iso = agora_iso()

    delivery_fixed = {
        "deliveredBy": delivery_data.get("deliveredBy", order_payload.get("delivered_by", "")),
        "deliveryDateTime": delivery_data.get("deliveryDateTime", order_payload.get("created_at", current_time_iso)),
        "mode": delivery_data.get("mode", order_payload.get("delivery_mode", "")),
        "pickupCode": delivery_data.get("pickupCode"),
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

    cardapio_web_order_id = str(order_payload.get("id"))

    transformed_base = {
        "id": cardapio_web_order_id,
        "orderId": cardapio_web_order_id,
        "displayId": str(order_payload.get("display_id", cardapio_web_order_id)),
        "orderType": order_payload.get("order_type", "DELIVERY").upper(),
        "salesChannel": order_payload.get("sales_channel", "CARDAPIOWEB").upper(),
        "orderTiming": order_payload.get("order_timing", "IMMEDIATE").upper(),
        "createdAt": order_payload.get("created_at", current_time_iso),
        "customer": customer_data,
        "delivery": delivery_fixed,
        "items": items_list,
        "merchant": {
            "id": str(order_payload.get("merchant_id", CARDAPIOWEB_MERCHANT_ID)),
            "name": order_payload.get("merchant_name", "Restaurante Padrão")
        },
        "total": float(order_payload.get("total", 0.0)),
        "payments": order_payload.get("payments", []),
        "status": "NEW",
        "fullCode": "PLACED",
        "code": "PLC"
    }
    return remove_null_values(transformed_base)

# ----------- ROTAS DA API -----------
@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        "status": "OK",
        "service": "Consumer-CardapioWeb API Bridge",
        "timestamp": agora_iso(),
        "version": "2.2.0", # Incremento de versão
        "pedidos_pendentes_count": len(PEDIDOS_PENDENTES),
        "pedidos_processados_count": len(PEDIDOS_PROCESSADOS)
    })

@app.route('/webhook/orders', methods=['POST'])
@app.route('/webhook/cardapioweb', methods=['POST'])
def webhook_orders():
    log_timestamp = agora_iso()
    event_data = request.get_json(silent=True)
    if not event_data:
        print(f"[{log_timestamp}] [WEBHOOK_ERROR] Payload JSON vazio ou malformado.")
        return jsonify({"error": "Payload JSON inválido ou ausente."}), 400
    
    print(f"[{log_timestamp}] [WEBHOOK_RECEIVED] Payload: {json.dumps(event_data, ensure_ascii=False)}")

    order_id_from_event = str(event_data.get("id") or event_data.get("order_id"))
    if not order_id_from_event or order_id_from_event == "None":
        print(f"[{log_timestamp}] [WEBHOOK_ERROR] 'id' ou 'order_id' ausente: {event_data}")
        return jsonify({"error": "'id' ou 'order_id' do pedido é obrigatório."}), 400

    is_simple_notification = "order_id" in event_data and len(event_data.keys()) <= 6 
    order_details_payload = event_data

    if is_simple_notification:
        url = f"{CARDAPIOWEB_BASE_URL}/orders/{order_id_from_event}"
        headers = {"X-API-KEY": CARDAPIOWEB_API_KEY, "Content-Type": "application/json"}
        params = {"merchant_id": CARDAPIOWEB_MERCHANT_ID}
        print(f"[{log_timestamp}] [WEBHOOK_FETCH] Buscando detalhes pedido {order_id_from_event}: {url}")
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            order_details_payload = response.json()
            print(f"[{log_timestamp}] [WEBHOOK_FETCH_SUCCESS] Detalhes obtidos para {order_id_from_event}")
        except requests.exceptions.RequestException as e:
            print(f"[{log_timestamp}] [WEBHOOK_FETCH_ERROR] Falha ao buscar {order_id_from_event}: {e}\n{traceback.format_exc()}")
            return jsonify({"error": f"Erro ao buscar detalhes no CardapioWeb: {str(e)}"}), 502

    try:
        pedido_transformado = transform_order_data(order_details_payload)
        internal_order_id = pedido_transformado["id"] 
        PEDIDOS_PENDENTES[internal_order_id] = pedido_transformado
        print(f"[{log_timestamp}] [WEBHOOK_SUCCESS] Pedido {internal_order_id} armazenado. Pendentes: {len(PEDIDOS_PENDENTES)}")
        return jsonify({
            "success": True,
            "orderId": internal_order_id,
            "message": "Pedido recebido e agendado para processamento."
        }), 200
    except Exception as e:
        print(f"[{log_timestamp}] [WEBHOOK_TRANSFORM_ERROR] Pedido {order_id_from_event}: {e}\n{traceback.format_exc()}")
        return jsonify({"error": f"Erro interno ao processar pedido: {str(e)}"}), 500

@app.route('/api/parceiro/polling', methods=['GET'])
def polling_orders():
    log_timestamp = agora_iso()
    if not verify_token(request): # 'request' é o proxy global do Flask
        return jsonify({"error": "Token de autenticação inválido ou ausente."}), 401
    
    try:
        pedidos_para_consumer = []
        for order_key in list(PEDIDOS_PENDENTES.keys()): 
            pedido = PEDIDOS_PENDENTES.get(order_key)
            if pedido:
                pedidos_para_consumer.append({
                    "id": pedido.get("id"),
                    "orderId": pedido.get("orderId"),
                    "createdAt": pedido.get("createdAt", log_timestamp), # fallback para agora_iso
                    "fullCode": pedido.get("fullCode", "PLACED"),
                    "code": pedido.get("code", "PLC")
                })
        
        print(f"[{log_timestamp}] [POLLING_SUCCESS] Retornando {len(pedidos_para_consumer)} pedidos.")
        return jsonify({
            "items": pedidos_para_consumer,
            "statusCode": 0,
            "reasonPhrase": None
        }), 200
    except Exception as e:
        print(f"[{log_timestamp}] [POLLING_ERROR] Erro: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "Erro interno ao processar polling.", "details": str(e)}), 500

@app.route('/api/parceiro/orders/<string:order_id>', methods=['GET'])
def get_order_details(order_id):
    log_timestamp = agora_iso()
    if not verify_token(request):
        return jsonify({"error": "Token de autenticação inválido ou ausente."}), 401
    
    try:
        order_id_str = str(order_id)
        pedido = PEDIDOS_PENDENTES.get(order_id_str)
        moved = False
        if not pedido:
            pedido = PEDIDOS_PROCESSADOS.get(order_id_str)

        if not pedido:
            print(f"[{log_timestamp}] [GET_ORDER_NOT_FOUND] Pedido {order_id_str} não encontrado.")
            return jsonify({"error": "Pedido não encontrado."}), 404

        print(f"[{log_timestamp}] [GET_ORDER_SUCCESS] Retornando detalhes do pedido {order_id_str}.")
        
        if order_id_str in PEDIDOS_PENDENTES:
            PEDIDOS_PROCESSADOS[order_id_str] = PEDIDOS_PENDENTES.pop(order_id_str)
            moved = True
            print(f"[{log_timestamp}] [GET_ORDER_MOVE] Pedido {order_id_str} movido para processados.")

        return jsonify(pedido), 200
    except Exception as e:
        print(f"[{log_timestamp}] [GET_ORDER_ERROR] Erro ao buscar {order_id}: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "Erro interno ao buscar pedido.", "details": str(e)}), 500

@app.route('/api/parceiro/orders/<string:order_id>/status', methods=['POST'])
def update_order_status(order_id):
    log_timestamp = agora_iso()
    if not verify_token(request):
        return jsonify({"error": "Token de autenticação inválido ou ausente."}), 401
    
    data = request.get_json(silent=True)
    if not data:
        print(f"[{log_timestamp}] [UPDATE_STATUS_ERROR] Payload JSON inválido para {order_id}.")
        return jsonify({"error": "Payload JSON obrigatório."}), 400

    new_status = data.get("status")
    if not new_status:
        print(f"[{log_timestamp}] [UPDATE_STATUS_ERROR] Campo 'status' ausente para {order_id}.")
        return jsonify({"error": "Campo 'status' é obrigatório."}), 400

    try:
        order_id_str = str(order_id)
        pedido_ref = None
        origin_dict_name = ""

        if order_id_str in PEDIDOS_PENDENTES:
            pedido_ref = PEDIDOS_PENDENTES[order_id_str]
            origin_dict_name = "PENDENTES"
        elif order_id_str in PEDIDOS_PROCESSADOS:
            pedido_ref = PEDIDOS_PROCESSADOS[order_id_str]
            origin_dict_name = "PROCESSADOS"

        if not pedido_ref:
            print(f"[{log_timestamp}] [UPDATE_STATUS_NOT_FOUND] Pedido {order_id_str} não encontrado.")
            return jsonify({"error": "Pedido não encontrado para atualização."}), 404

        pedido_ref["status"] = new_status
        pedido_ref["fullCode"] = data.get("fullCode", new_status.upper())
        pedido_ref["code"] = data.get("code", new_status[:3].upper())
        pedido_ref["updatedAt"] = log_timestamp # usa o timestamp do início da requisição

        # Se o status for final (ex: CONCLUIDO, CANCELADO), pode mover para processados se ainda em pendentes.
        # Isso depende da sua lógica de fluxo de pedidos.
        if origin_dict_name == "PENDENTES" and new_status.upper() in ["CONFIRMED", "DISPATCHED", "DELIVERED", "CONCLUDED", "CANCELLED"]:
             PEDIDOS_PROCESSADOS[order_id_str] = PEDIDOS_PENDENTES.pop(order_id_str)
             print(f"[{log_timestamp}] [UPDATE_STATUS_MOVE] Pedido {order_id_str} movido para processados após status: {new_status}.")
        
        print(f"[{log_timestamp}] [UPDATE_STATUS_SUCCESS] Status do pedido {order_id_str} ({origin_dict_name}) atualizado para: {new_status}.")
        return jsonify({
            "success": True,
            "orderId": order_id_str,
            "status": pedido_ref["status"],
            "updatedAt": pedido_ref["updatedAt"]
        }), 200
    except Exception as e:
        print(f"[{log_timestamp}] [UPDATE_STATUS_ERROR] Erro ao atualizar {order_id}: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "Erro interno ao atualizar status.", "details": str(e)}), 500

# TODO: Implementar novo endpoint para receber alterações do PDV vindas do Consumer,
#       conforme mencionado por Yuri. Exemplo:
# @app.route('/api/parceiro/pdv/changes', methods=['POST'])
# def pdv_changes_webhook():
# if not verify_token(request):
# return jsonify({"error": "Token inválido"}), 401
# pdv_update_data = request.get_json(silent=True)
# # Processar pdv_update_data (ex: encaminhar para CardapioWeb se necessário)
# print(f"[{agora_iso()}] [PDV_CHANGES] Recebida alteração do PDV: {pdv_update_data}")
# return jsonify({"success": True, "message": "Alteração do PDV recebida."}), 200

# ----------- ENDPOINTS DE DEBUG (Opcional) -----------
@app.route('/api/debug/orders', methods=['GET'])
def debug_list_orders():
    # TODO: Proteger este endpoint em produção (ex: token de admin, IP restrito)
    return jsonify({
        "pedidos_pendentes_count": len(PEDIDOS_PENDENTES),
        "pedidos_processados_count": len(PEDIDOS_PROCESSADOS),
        "ids_pedidos_pendentes": list(PEDIDOS_PENDENTES.keys()),
        "ids_pedidos_processados": list(PEDIDOS_PROCESSADOS.keys()),
        "timestamp": agora_iso()
    })

@app.route('/api/debug/clear', methods=['POST'])
def debug_clear_orders():
    # TODO: Proteger este endpoint em produção
    global PEDIDOS_PENDENTES, PEDIDOS_PROCESSADOS
    PEDIDOS_PENDENTES.clear()
    PEDIDOS_PROCESSADOS.clear()
    print(f"[{agora_iso()}] [DEBUG_CLEAR] Todos os pedidos em memória foram limpos.")
    return jsonify({"success": True, "message": "Todos os pedidos em memória foram limpos."})

# ----------- TRATAMENTO DE OPTIONS (CORS Preflight) -----------
# O @app.after_request já adiciona os headers globais.
# Rotas explícitas para OPTIONS podem ser mantidas para garantir compatibilidade.
@app.route('/api/parceiro/polling', methods=['OPTIONS'])
@app.route('/api/parceiro/orders/<string:order_id>', methods=['OPTIONS'])
@app.route('/api/parceiro/orders/<string:order_id>/status', methods=['OPTIONS'])
def handle_options_requests(order_id=None):
    return '', 204 # No Content, os headers são adicionados pelo after_request

# ----------- ERROR HANDLERS GLOBAIS -----------
@app.errorhandler(400)
def bad_request_error(error):
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [ERROR_400] Bad Request: {error}")
    return jsonify({"error": "Requisição inválida.", "details": str(error.description if hasattr(error, 'description') else error)}), 400

@app.errorhandler(401)
def unauthorized_error(error):
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [ERROR_401] Unauthorized: {error}")
    return jsonify({"error": "Não autorizado. Token inválido ou ausente.", "details": str(error.description if hasattr(error, 'description') else error)}), 401

@app.errorhandler(404)
def not_found_error(error):
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [ERROR_404] Not Found: {request.path} - {error}")
    return jsonify({"error": "Recurso não encontrado.", "endpoint": request.path}), 404

@app.errorhandler(500)
def internal_server_error(error): # Este handler lida com exceções não capturadas nas rotas
    log_timestamp = agora_iso()
    # O traceback já deve ter sido logado pela rota se a exceção ocorreu lá.
    # Se a exceção ocorreu antes da rota (ex: erro no framework), logamos aqui.
    print(f"[{log_timestamp}] [ERROR_500] Internal Server Error: {error}\n{traceback.format_exc()}")
    return jsonify({"error": "Erro interno do servidor.", "details": str(error)}), 500

# ----------- EXECUÇÃO (Para desenvolvimento local) -----------
if __name__ == '__main__':
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [STARTUP] Iniciando Consumer-CardapioWeb API Bridge v2.2.0 (Dev Mode)")
    # Para deploy em produção (como no Fly.io), use um servidor WSGI como Gunicorn.
    # Ex: gunicorn -w 4 -b 0.0.0.0:8080 app:app
    # O debug=True do Flask não é recomendado para produção.
    app.run(debug=False, host='0.0.0.0', port=8080)
