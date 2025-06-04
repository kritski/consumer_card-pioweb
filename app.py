import requests
import re
import json
import traceback # Para logging detalhado de exceções
from datetime import datetime, timezone
from urllib.parse import urlunparse, urlparse
from flask import Flask, request, jsonify, abort, redirect

# Inicialização da aplicação Flask
app = Flask(__name__)

# ----------- CONFIGURAÇÃO ANTI-GZIP (SOMENTE PARA RESPOSTAS DA API) -----------
@app.after_request
def after_request_handler(response):
    response.headers.pop('Content-Encoding', None)
    response.headers.pop('Vary', None) 

    if response.content_type and response.content_type.startswith('application/json'):
        response.headers['Content-Type'] = 'application/json; charset=utf-8'

    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Api-Key')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
    return response

@app.before_request
def canonicalize_url_redirect():
    path = request.path
    if '//' in path:
        canonical_path = re.sub(r'/+', '/', path)
        parsed_url = urlparse(request.url)
        canonical_url = urlunparse(parsed_url._replace(path=canonical_path))
        return redirect(canonical_url, code=308)

# ----------- CONFIGURAÇÕES GLOBAIS -----------
CARDAPIOWEB_BASE_URL = 'https://integracao.cardapioweb.com/api/partner/v1'
CARDAPIOWEB_API_KEY = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5'
CARDAPIOWEB_MERCHANT_ID = '14104'
CONSUMER_API_TOKEN = 'pk_live_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy'

PEDIDOS_PENDENTES = {}
PEDIDOS_PROCESSADOS = {}

# ----------- FUNÇÕES AUXILIARES -----------
def agora_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def _remove_null_values_recursive(obj_data):
    if isinstance(obj_data, dict):
        return {k: _remove_null_values_recursive(v) for k, v in obj_data.items() if v is not None}
    elif isinstance(obj_data, list):
        new_list = [_remove_null_values_recursive(i) for i in obj_data]
        return [item for item in new_list if item is not None]
    return obj_data

def remove_null_values(obj_data):
    return _remove_null_values_recursive(obj_data)

def verify_token(current_request):
    token_x_api_key = current_request.headers.get("X-Api-Key")
    token_xapikey_variant = current_request.headers.get("XApiKey")
    token_auth_header = current_request.headers.get("Authorization")
    log_timestamp = agora_iso()

    if token_x_api_key and token_x_api_key == CONSUMER_API_TOKEN: return True
    if token_xapikey_variant and token_xapikey_variant == CONSUMER_API_TOKEN: return True
    if token_auth_header:
        scheme, _, token_value = token_auth_header.partition(' ')
        if scheme.lower() == "bearer" and token_value == CONSUMER_API_TOKEN: return True
    
    print(f"[{log_timestamp}] [AUTH_FAIL] Token inválido. X-Api-Key: '{token_x_api_key}', XApiKey: '{token_xapikey_variant}', Authorization: '{token_auth_header}'")
    return False

def format_timestamp_for_consumer(timestamp_str):
    if not timestamp_str: return None
    try:
        dt_obj = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        return dt_obj.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    except ValueError:
        if isinstance(timestamp_str, str) and timestamp_str.endswith('Z'): return timestamp_str
        print(f"[WARN] Timestamp '{timestamp_str}' não pôde ser normalizado para o formato ISO com Z.")
        return timestamp_str

def transform_order_data_for_consumer_details(cw_payload):
    # (Esta função permanece a mesma da v2.6.1, com todos os TODOs para você revisar)
    # ... (código da função transform_order_data_for_consumer_details da v2.6.1 aqui) ...
    # Vou colar ela aqui para manter o código completo
    current_time_iso = agora_iso()
    cw_customer_data = cw_payload.get("customer", cw_payload.get("Usuario", {}))
    phone_number_from_cw = None
    if isinstance(cw_customer_data.get("phone"), dict):
        phone_number_from_cw = cw_customer_data.get("phone", {}).get("number")
    elif isinstance(cw_customer_data.get("telefone"), str):
         phone_number_from_cw = cw_customer_data.get("telefone")
    elif isinstance(cw_customer_data.get("phone"), str):
         phone_number_from_cw = cw_customer_data.get("phone")
    customer_name_from_cw = cw_customer_data.get("name")
    if not customer_name_from_cw and cw_customer_data.get("nome"):
        customer_name_from_cw = f"{cw_customer_data.get('nome', '')} {cw_customer_data.get('sobrenome', '')}".strip()
    customer_details = {
        "id": str(cw_customer_data.get("id", "")),
        "name": customer_name_from_cw if customer_name_from_cw else "Cliente não informado",
        "phone": {
            "number": phone_number_from_cw if phone_number_from_cw else "",
            "localizer": None, 
            "localizerExpiration": None
        },
        "documentNumber": cw_customer_data.get("cpf", cw_customer_data.get("document", None))
    }
    cw_delivery_data = cw_payload.get("delivery", cw_payload.get("Entrega", {}))
    cw_address_data = cw_delivery_data.get("deliveryAddress", cw_delivery_data)
    delivery_details = {
        "mode": cw_delivery_data.get("mode", "DEFAULT").upper(),
        "deliveredBy": cw_delivery_data.get("deliveredBy", "PARTNER").capitalize(),
        "pickupCode": cw_delivery_data.get("pickupCode", None),
        "deliveryDateTime": format_timestamp_for_consumer(cw_delivery_data.get("deliveryDateTime", cw_payload.get("transito_em", cw_payload.get("createdAt", current_time_iso)))),
        "deliveryAddress": {
            "country": cw_address_data.get("country", "BR"),
            "state": cw_address_data.get("estado", cw_address_data.get("state", "")),
            "city": cw_address_data.get("cidade", cw_address_data.get("city", "")),
            "postalCode": cw_address_data.get("cep", cw_address_data.get("postalCode", "")),
            "streetName": cw_address_data.get("endereco", cw_address_data.get("streetName", "")),
            "streetNumber": cw_address_data.get("numero", cw_address_data.get("streetNumber", "")),
            "neighborhood": cw_address_data.get("bairro", cw_address_data.get("neighborhood", "")),
            "complement": cw_address_data.get("complemento", None),
            "reference": cw_address_data.get("reference", None),
            "formattedAddress": None, # TODO: YURI REVISAR
            "coordinates": None     # TODO: YURI REVISAR
        },
        "observations": cw_delivery_data.get("obs", None)
    }
    items_details_for_consumer = []
    cw_items_list = cw_payload.get("items", cw_payload.get("Itens", []))
    for item_index, cw_item_data in enumerate(cw_items_list):
        options_for_consumer = []
        cw_item_options = cw_item_data.get("options", cw_item_data.get("Complementos", []))
        if cw_item_options:
            for opt_index, cw_opt_data in enumerate(cw_item_options):
                option_name = cw_opt_data.get("name", cw_opt_data.get("nome_complemento"))
                option_qty = int(cw_opt_data.get("quantity", 1))
                option_unit_price = float(cw_opt_data.get("unitPrice", cw_opt_data.get("valor", 0.0)))
                options_for_consumer.append({
                    "id": str(cw_opt_data.get("optionId", cw_opt_data.get("id_complemento", ""))),
                    "name": option_name, "quantity": option_qty, "unitPrice": option_unit_price,
                    "price": option_unit_price * option_qty,
                    "externalCode": cw_opt_data.get("externalCode", None), # TODO: YURI REVISAR
                    "unit": "UN", # TODO: YURI REVISAR
                    "ean": None,  # TODO: YURI REVISAR
                    "index": opt_index,
                    "addition": 0 # TODO: YURI REVISAR
                })
        item_unit_price = float(cw_item_data.get("unitPrice", cw_item_data.get("valor_unitario", 0.0)))
        item_quantity = int(cw_item_data.get("quantity", 1))
        item_total_price = item_unit_price * item_quantity
        item_options_price = sum(opt.get("price", 0.0) for opt in options_for_consumer)
        items_details_for_consumer.append({
            "id": str(cw_item_data.get("id", cw_item_data.get("id_produto", ""))),
            "externalCode": cw_item_data.get("externalCode", None), # TODO: YURI REVISAR
            "name": cw_item_data.get("name", cw_item_data.get("nome_produto", "")),
            "quantity": item_quantity, "unitPrice": item_unit_price, "totalPrice": item_total_price,
            "price": item_total_price,
            "observations": cw_item_data.get("observations", cw_item_data.get("obs", None)),
            "imageUrl": cw_item_data.get("imageUrl", None), # TODO: YURI REVISAR
            "options": options_for_consumer if options_for_consumer else None,
            "index": item_index + 1, "unit": "UN", # TODO: YURI REVISAR
            "ean": None,  # TODO: YURI REVISAR
            "uniqueId": None, # TODO: YURI REVISAR
            "optionsPrice": item_options_price,
            "addition": 0, # TODO: YURI REVISAR
            "scalePrices": None # TODO: YURI REVISAR
        })
    cw_payments_list = cw_payload.get("payments", [])
    payment_methods_for_consumer = []
    total_order_amount_from_payments = 0.0
    for cw_pay_method in cw_payments_list:
        method_type_consumer = str(cw_pay_method.get("payment_method", "OTHER")).upper() # TODO: YURI REVISAR MAPEAMENTO
        card_details_consumer = None
        if "card" in cw_pay_method or "card_brand" in cw_pay_method:
            card_brand_from_cw = None
            if isinstance(cw_pay_method.get("card"), dict): card_brand_from_cw = cw_pay_method.get("card", {}).get("brand")
            elif isinstance(cw_pay_method.get("card_brand"), str): card_brand_from_cw = cw_pay_method.get("card_brand")
            card_details_consumer = { "brand": card_brand_from_cw } if card_brand_from_cw else None
        payment_value = float(cw_pay_method.get("total", cw_pay_method.get("value", 0.0)))
        total_order_amount_from_payments += payment_value
        payment_methods_for_consumer.append({
            "method": method_type_consumer,
            "type": str(cw_pay_method.get("payment_type", "OFFLINE")).upper(),
            "currency": "BRL", "value": payment_value, "card": card_details_consumer
        })
    final_order_total = float(cw_payload.get("total", 0.0))
    prepaid_amount_calculated = 0.0 # TODO: YURI REVISAR LÓGICA
    for pm in payment_methods_for_consumer:
        if pm.get("type") == "ONLINE": prepaid_amount_calculated += pm.get("value", 0.0)
    pending_amount_calculated = max(0, final_order_total - prepaid_amount_calculated)
    payments_for_consumer = {
        "methods": payment_methods_for_consumer,
        "pending": pending_amount_calculated, "prepaid": prepaid_amount_calculated
    }
    cw_delivery_fee = float(cw_payload.get("delivery_fee", 0.0)) # TODO: YURI REVISAR
    cw_benefits_or_discount = float(cw_payload.get("discount_amount", 0.0)) # TODO: YURI REVISAR
    cw_additional_fees = float(cw_payload.get("additional_fees", 0.0)) # TODO: YURI REVISAR
    sub_total_for_consumer = final_order_total - cw_delivery_fee - cw_additional_fees + cw_benefits_or_discount # TODO: YURI REVISAR CÁLCULO/OBTENÇÃO
    total_for_consumer = {
        "subTotal": sub_total_for_consumer, "deliveryFee": cw_delivery_fee,
        "orderAmount": final_order_total, "benefits": cw_benefits_or_discount,
        "additionalFees": cw_additional_fees
    }
    merchant_for_consumer = {
        "id": str(cw_payload.get("merchant_id", CARDAPIOWEB_MERCHANT_ID)),
        "name": cw_payload.get("merchant_name", "Restaurante Padrão")
    }
    order_item_for_consumer = {
        "id": str(cw_payload.get("id")),
        "displayId": str(cw_payload.get("display_id", cw_payload.get("ref", cw_payload.get("id")))),
        "orderType": cw_payload.get("order_type", "DELIVERY").upper(),
        "salesChannel": cw_payload.get("sales_channel", "PARTNER").upper(),
        "orderTiming": cw_payload.get("order_timing", "IMMEDIATE").upper(),
        "createdAt": format_timestamp_for_consumer(cw_payload.get("created_at", cw_payload.get("aceito_em", current_time_iso))),
        "preparationStartDateTime": format_timestamp_for_consumer(cw_payload.get("preparation_start_time", cw_payload.get("producao_em", current_time_iso))),
        "merchant": merchant_for_consumer, "total": total_for_consumer, "payments": payments_for_consumer,
        "customer": customer_details, "delivery": delivery_details, "items": items_details_for_consumer,
        "benefits": None, "picking": None, "extraInfo": None, "schedule": None, "indoor": None, "takeout": None # TODOs YURI REVISAR
    }
    return remove_null_values(order_item_for_consumer)

# ----------- ROTAS DA API -----------
@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        "status": "OK",
        "service": "Consumer-CardapioWeb API Bridge",
        "timestamp": agora_iso(),
        "version": "2.6.2", # Polling response simplificada
        "pedidos_pendentes_count": len(PEDIDOS_PENDENTES),
        "pedidos_processados_count": len(PEDIDOS_PROCESSADOS)
    })

@app.route('/webhook/orders', methods=['POST'])
@app.route('/webhook/cardapioweb', methods=['POST'])
def webhook_orders():
    # ... (função webhook_orders mantida como na v2.6.1) ...
    log_timestamp = agora_iso()
    event_data = request.get_json(silent=True)
    if not event_data:
        print(f"[{log_timestamp}] [WEBHOOK_ERROR] Payload JSON vazio ou malformado.")
        return jsonify({"error": "Payload JSON inválido ou ausente."}), 400
    order_id_from_event = str(event_data.get("id") or event_data.get("order_id"))
    if not order_id_from_event or order_id_from_event == "None":
        print(f"[{log_timestamp}] [WEBHOOK_ERROR] 'id' ou 'order_id' ausente. Payload: {event_data}")
        return jsonify({"error": "'id' ou 'order_id' do pedido é obrigatório."}), 400
    print(f"[{log_timestamp}] [WEBHOOK_RECEIVED] Evento para order_id: {order_id_from_event}")
    order_payload_from_cardapioweb = event_data
    is_simple_notification = "order_id" in event_data and len(event_data.keys()) <= 6 
    if is_simple_notification:
        url = f"{CARDAPIOWEB_BASE_URL}/orders/{order_id_from_event}"
        headers = {"X-API-KEY": CARDAPIOWEB_API_KEY, "Content-Type": "application/json"}
        params = {"merchant_id": CARDAPIOWEB_MERCHANT_ID}
        print(f"[{log_timestamp}] [WEBHOOK_FETCH] Buscando detalhes pedido {order_id_from_event}")
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            order_payload_from_cardapioweb = response.json()
            print(f"[{log_timestamp}] [WEBHOOK_FETCH_SUCCESS] Detalhes obtidos para {order_id_from_event}")
        except requests.exceptions.RequestException as e:
            print(f"[{log_timestamp}] [WEBHOOK_FETCH_ERROR] Falha ao buscar {order_id_from_event}: {e}\n{traceback.format_exc()}")
            return jsonify({"error": f"Erro ao buscar detalhes no CardapioWeb: {str(e)}"}), 502
    try:
        PEDIDOS_PENDENTES[order_id_from_event] = order_payload_from_cardapioweb
        print(f"[{log_timestamp}] [WEBHOOK_SUCCESS] Pedido {order_id_from_event} (payload CardapioWeb) armazenado. Pendentes: {len(PEDIDOS_PENDENTES)}")
        return jsonify({"success": True, "orderId": order_id_from_event, "message": "Pedido recebido e dados brutos armazenados."}), 200
    except Exception as e:
        print(f"[{log_timestamp}] [WEBHOOK_STORE_ERROR] Pedido {order_id_from_event}: {e}\n{traceback.format_exc()}")
        return jsonify({"error": f"Erro interno ao armazenar dados do pedido: {str(e)}"}), 500


@app.route('/api/parceiro/polling', methods=['GET'])
def polling_orders():
    log_timestamp = agora_iso()
    if not verify_token(request):
        return jsonify({"error": "Token de autenticação inválido ou ausente."}), 401
    
    try:
        items_para_polling = []
        for order_key, order_payload_cw in list(PEDIDOS_PENDENTES.items()): 
            items_para_polling.append({
                "id":       str(order_payload_cw.get("id")),
                "orderId":  str(order_payload_cw.get("id")), 
                "createdAt": format_timestamp_for_consumer(order_payload_cw.get("created_at", order_payload_cw.get("aceito_em", log_timestamp))), 
                "fullCode": "PLACED",
                "code":     "PLC"
            })
        
        print(f"[{log_timestamp}] [POLLING_SUCCESS] Retornando {len(items_para_polling)} pedidos no formato resumido.")
        # ALTERAÇÃO: Removendo statusCode e reasonPhrase do corpo do polling,
        # retornando apenas a lista de items, como no seu código do GitHub que funcionava.
        # Se a documentação do Consumer para polling EXIGIR statusCode e reasonPhrase,
        # precisaremos voltar para a estrutura anterior para esta rota.
        return jsonify({"items": items_para_polling}), 200

    except Exception as e:
        print(f"[{log_timestamp}] [POLLING_ERROR] Erro: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "Erro interno ao processar polling.", "details": str(e)}), 500

@app.route('/api/parceiro/orders/<string:order_id>', methods=['GET'])
def get_order_details(order_id):
    # ... (função get_order_details mantida como na v2.6.1, já retorna a estrutura correta com "item") ...
    log_timestamp = agora_iso()
    if not verify_token(request):
        return jsonify({"error": "Token de autenticação inválido ou ausente."}), 401
    try:
        order_id_str = str(order_id)
        pedido_payload_cardapioweb = PEDIDOS_PENDENTES.get(order_id_str)
        is_from_pending = True
        if not pedido_payload_cardapioweb:
            pedido_payload_cardapioweb = PEDIDOS_PROCESSADOS.get(order_id_str)
            is_from_pending = False
        if not pedido_payload_cardapioweb:
            print(f"[{log_timestamp}] [GET_ORDER_NOT_FOUND] Payload original do pedido {order_id_str} não encontrado.")
            return jsonify({"item": None, "statusCode": 1, "reasonPhrase": "Pedido não encontrado"}), 404
        pedido_detalhado_consumer_format = transform_order_data_for_consumer_details(pedido_payload_cardapioweb)
        print(f"[{log_timestamp}] [GET_ORDER_SUCCESS] Retornando detalhes do pedido {order_id_str} no formato Consumer.")
        if is_from_pending and order_id_str in PEDIDOS_PENDENTES:
            PEDIDOS_PROCESSADOS[order_id_str] = PEDIDOS_PENDENTES.pop(order_id_str)
            print(f"[{log_timestamp}] [GET_ORDER_MOVE] Payload original do pedido {order_id_str} movido para processados.")
        response_data = {
            "item": pedido_detalhado_consumer_format,
            "statusCode": 0, "reasonPhrase": None
        }
        return jsonify(response_data), 200
    except Exception as e:
        print(f"[{log_timestamp}] [GET_ORDER_ERROR] Erro ao buscar/transformar {order_id}: {e}\n{traceback.format_exc()}")
        return jsonify({"item": None, "error": "Erro interno ao buscar detalhes do pedido.", "details": str(e), "statusCode": 2, "reasonPhrase": "Internal Server Error"}), 500

@app.route('/api/parceiro/orders/<string:order_id>/status', methods=['POST'])
def update_order_status(order_id):
    # ... (função update_order_status mantida como na v2.6.1) ...
    log_timestamp = agora_iso()
    if not verify_token(request):
        return jsonify({"error": "Token de autenticação inválido ou ausente."}), 401
    data = request.get_json(silent=True)
    if not data:
        print(f"[{log_timestamp}] [UPDATE_STATUS_ERROR] Payload JSON inválido para {order_id}.")
        return jsonify({"error": "Payload JSON obrigatório."}), 400
    new_status_from_consumer = data.get("status")
    if not new_status_from_consumer:
        print(f"[{log_timestamp}] [UPDATE_STATUS_ERROR] Campo 'status' ausente para {order_id}.")
        return jsonify({"error": "Campo 'status' é obrigatório."}), 400
    try:
        order_id_str = str(order_id)
        pedido_payload_cardapioweb = PEDIDOS_PENDENTES.get(order_id_str)
        dict_source = PEDIDOS_PENDENTES
        if not pedido_payload_cardapioweb:
            pedido_payload_cardapioweb = PEDIDOS_PROCESSADOS.get(order_id_str)
            dict_source = PEDIDOS_PROCESSADOS
        if not pedido_payload_cardapioweb:
            print(f"[{log_timestamp}] [UPDATE_STATUS_NOT_FOUND] Pedido {order_id_str} não encontrado.")
            return jsonify({"error": "Pedido não encontrado para atualização."}), 404
        print(f"[{log_timestamp}] [UPDATE_STATUS_RECEIVED] Status do pedido {order_id_str} recebido do Consumer: {new_status_from_consumer}. Payload: {data}")
        if '_consumer_integration_status' not in pedido_payload_cardapioweb:
            pedido_payload_cardapioweb['_consumer_integration_status'] = {}
        pedido_payload_cardapioweb['_consumer_integration_status']['status'] = new_status_from_consumer
        pedido_payload_cardapioweb['_consumer_integration_status']['fullCode'] = data.get("fullCode", new_status_from_consumer.upper())
        pedido_payload_cardapioweb['_consumer_integration_status']['code'] = data.get("code", new_status_from_consumer[:3].upper())
        pedido_payload_cardapioweb['_consumer_integration_status']['updatedAt'] = log_timestamp
        if order_id_str in PEDIDOS_PENDENTES:
            PEDIDOS_PENDENTES[order_id_str] = pedido_payload_cardapioweb
            if new_status_from_consumer.upper() in ["CONFIRMED", "DISPATCHED", "DELIVERED", "CONCLUDED", "CANCELLED", "CANCELED"]:
                 PEDIDOS_PROCESSADOS[order_id_str] = PEDIDOS_PENDENTES.pop(order_id_str)
                 print(f"[{log_timestamp}] [UPDATE_STATUS_MOVE] Payload do pedido {order_id_str} movido para processados.")
        elif order_id_str in PEDIDOS_PROCESSADOS:
            PEDIDOS_PROCESSADOS[order_id_str] = pedido_payload_cardapioweb
        return jsonify({"message": "Status do pedido recebido com sucesso.", "orderId": order_id_str, "newStatus": new_status_from_consumer }), 200
    except Exception as e:
        print(f"[{log_timestamp}] [UPDATE_STATUS_ERROR] Erro ao atualizar {order_id}: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "Erro interno ao atualizar status.", "details": str(e)}), 500

# ----------- ENDPOINTS DE DEBUG (Opcional) -----------
@app.route('/api/debug/orders', methods=['GET'])
def debug_list_orders():
    return jsonify({
        "pedidos_pendentes_count": len(PEDIDOS_PENDENTES),
        "pedidos_processados_count": len(PEDIDOS_PROCESSADOS),
        "ids_pedidos_pendentes": list(PEDIDOS_PENDENTES.keys()),
        "ids_pedidos_processados": list(PEDIDOS_PROCESSADOS.keys()),
        "timestamp": agora_iso()
    })

@app.route('/api/debug/clear', methods=['POST'])
def debug_clear_orders():
    global PEDIDOS_PENDENTES, PEDIDOS_PROCESSADOS
    PEDIDOS_PENDENTES.clear()
    PEDIDOS_PROCESSADOS.clear()
    print(f"[{agora_iso()}] [DEBUG_CLEAR] Todos os pedidos em memória foram limpos.")
    return jsonify({"success": True, "message": "Todos os pedidos em memória foram limpos."})

# ----------- TRATAMENTO DE OPTIONS (CORS Preflight) -----------
@app.route('/api/parceiro/polling', methods=['OPTIONS'])
@app.route('/api/parceiro/orders/<string:order_id>', methods=['OPTIONS'])
@app.route('/api/parceiro/orders/<string:order_id>/status', methods=['OPTIONS'])
def handle_options_requests(order_id=None):
    return '', 204

# ----------- ERROR HANDLERS GLOBAIS -----------
@app.errorhandler(400)
def handle_bad_request(e):
    log_timestamp = agora_iso()
    desc = e.description if hasattr(e, 'description') else str(e)
    print(f"[{log_timestamp}] [ERROR_400] Bad Request: {desc}")
    return jsonify(error="Requisição inválida.", details=desc, statusCode=3, reasonPhrase="Bad Request"), 400

@app.errorhandler(401)
def handle_unauthorized(e): 
    log_timestamp = agora_iso()
    desc = e.description if hasattr(e, 'description') else "Token de autenticação inválido ou ausente."
    print(f"[{log_timestamp}] [ERROR_HANDLER_401] Unauthorized access attempt. Description: {desc}")
    return jsonify(error="Não autorizado.", details=desc, statusCode=4, reasonPhrase="Unauthorized"), 401

@app.errorhandler(404)
def handle_not_found(e):
    log_timestamp = agora_iso()
    desc = e.description if hasattr(e, 'description') else str(e)
    print(f"[{log_timestamp}] [ERROR_404] Not Found: {request.path}. Details: {desc}")
    return jsonify(error="Recurso não encontrado.", endpoint=request.path, statusCode=1, reasonPhrase="Not Found"), 404

@app.errorhandler(500)
def handle_internal_server_error(e_internal): 
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [ERROR_HANDLER_500] Internal Server Error: {e_internal}\n{traceback.format_exc()}")
    return jsonify(error="Erro interno do servidor.", details=str(e_internal), statusCode=2, reasonPhrase="Internal Server Error"), 500

@app.errorhandler(Exception) 
def handle_generic_exception(e_generic): 
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [ERROR_HANDLER_GENERIC] Unhandled Exception: {e_generic}\n{traceback.format_exc()}")
    if hasattr(e_generic, 'code') and isinstance(e_generic.code, int) and 400 <= e_generic.code < 600: 
        return jsonify(error=str(e_generic.name if hasattr(e_generic, 'name') else type(e_generic).__name__), 
                       details=str(e_generic.description if hasattr(e_generic, 'description') else e_generic), 
                       statusCode=5, reasonPhrase="Unhandled HTTP Exception"), e_generic.code
    return jsonify(error="Erro inesperado no servidor.", statusCode=2, reasonPhrase="Unexpected Server Error"), 500

# ----------- EXECUÇÃO -----------
if __name__ == '__main__':
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [STARTUP] Iniciando Consumer-CardapioWeb API Bridge v2.6.2 (Dev Mode)")
    app.run(debug=False, host='0.0.0.0', port=8080)
