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
        return [_remove_null_values_recursive(i) for i in obj_data if i is not None]
    return obj_data

def remove_null_values(obj_data):
    return _remove_null_values_recursive(obj_data)

def verify_token(current_request):
    token_x_api_key = current_request.headers.get("X-Api-Key")
    token_xapikey_variant = current_request.headers.get("XApiKey")
    token_auth_header = current_request.headers.get("Authorization")
    log_timestamp = agora_iso()

    if token_x_api_key and token_x_api_key == CONSUMER_API_TOKEN:
        return True
    if token_xapikey_variant and token_xapikey_variant == CONSUMER_API_TOKEN:
        return True
    if token_auth_header:
        scheme, _, token_value = token_auth_header.partition(' ')
        if scheme.lower() == "bearer" and token_value == CONSUMER_API_TOKEN:
            return True
    
    print(f"[{log_timestamp}] [AUTH_FAIL] Token inválido ou ausente. X-Api-Key: '{token_x_api_key}', XApiKey: '{token_xapikey_variant}', Authorization: '{token_auth_header}'")
    return False

def transform_order_data_for_consumer_details(order_payload_from_cardapioweb):
    """
    Transforma o payload do pedido do CardapioWeb para a estrutura DETALHADA
    esperada pelo endpoint de detalhes do pedido do Consumer.
    """
    cw = order_payload_from_cardapioweb # Alias para facilitar
    current_time_iso = agora_iso()

    # CUSTOMER
    cw_customer = cw.get("customer", {})
    cw_phone = cw_customer.get("phone", {}) # Assumindo que o CardapioWeb já pode retornar um objeto phone
    phone_number = ""
    if isinstance(cw_phone, str):
        phone_number = cw_phone
    elif isinstance(cw_phone, dict):
        phone_number = cw_phone.get("number", "")

    customer_details = {
        "id": str(cw_customer.get("id", "")), # TODO: Mapear do CardapioWeb se disponível, senão gerar UUID?
        "name": cw_customer.get("name", "Nome não informado"),
        "phone": {
            "number": phone_number,
            "localizer": None, # TODO: Mapear do CardapioWeb se disponível
            "localizerExpiration": None # TODO: Mapear do CardapioWeb se disponível
        },
        "documentNumber": cw_customer.get("document", None) # TODO: Verificar nome do campo no CardapioWeb
    }

    # DELIVERY
    cw_delivery = cw.get("delivery", {})
    cw_address = cw_delivery.get("deliveryAddress", cw.get("delivery_address", {}))
    delivery_details = {
        "mode": cw_delivery.get("mode", cw.get("delivery_mode", "DEFAULT")).upper(), # DEFAULT é um valor comum
        "deliveredBy": cw_delivery.get("deliveredBy", cw.get("delivered_by", "PARTNER")).capitalize(), # Partner ou Merchant
        "pickupCode": cw_delivery.get("pickupCode", None),
        "deliveryDateTime": cw_delivery.get("deliveryDateTime", cw.get("created_at", current_time_iso)), # TODO: Confirmar se é a data da entrega ou do pedido
        "deliveryAddress": {
            "country": cw_address.get("country", "BR"), # Usar código do país se possível
            "state": cw_address.get("state", ""),
            "city": cw_address.get("city", ""),
            "postalCode": cw_address.get("postalCode", cw_address.get("postal_code", "")),
            "streetName": cw_address.get("streetName", cw_address.get("street", "")),
            "streetNumber": cw_address.get("streetNumber", cw_address.get("number", "")),
            "neighborhood": cw_address.get("neighborhood", ""),
            "complement": cw_address.get("complement", None),
            "reference": cw_address.get("reference", None),
            "formattedAddress": None, # TODO: Montar ou mapear do CardapioWeb se disponível
            "coordinates": None # TODO: Mapear {latitude, longitude} do CardapioWeb se disponível
        },
        "observations": cw_delivery.get("observations", None) # TODO: Verificar se há campo de observação de entrega
    }

    # ITEMS e OPTIONS
    items_details = []
    for cw_item in cw.get("items", []):
        options_details = []
        if cw_item.get("options"):
            for cw_opt in cw_item.get("options", []):
                # Documentação Consumer para options: unitPrice, unit, ean, quantity, externalCode, price, name, index, id, addition
                # Seu fix_option antigo: optionId, externalCode, name, optionGroupId, optionGroupName, quantity, unitPrice
                # Precisamos de um mapeamento cuidadoso aqui.
                options_details.append({
                    "id": str(cw_opt.get("option_id", cw_opt.get("optionId", ""))), # TODO: Confirmar ID da opção
                    "name": cw_opt.get("name", ""),
                    "quantity": int(cw_opt.get("quantity", 1)),
                    "unitPrice": float(cw_opt.get("unit_price", cw_opt.get("unitPrice", 0))),
                    "price": float(cw_opt.get("unit_price", cw_opt.get("unitPrice", 0))) * int(cw_opt.get("quantity", 1)), # total da opção
                    "externalCode": cw_opt.get("external_code", cw_opt.get("externalCode", None)),
                    "unit": "UN", # TODO: Mapear do CardapioWeb se disponível, senão padrão
                    "ean": None, # TODO: Mapear do CardapioWeb se disponível
                    "index": 0, # TODO: Se o CardapioWeb fornecer um índice/ordem
                    "addition": 0 # TODO: Se for um adicional, o valor pode vir aqui
                })
        
        items_details.append({
            "id": str(cw_item.get("item_id", cw_item.get("id", ""))),
            "externalCode": cw_item.get("external_code", cw_item.get("externalCode", None)),
            "name": cw_item.get("name", ""),
            "quantity": int(cw_item.get("quantity", 1)),
            "unitPrice": float(cw_item.get("unit_price", cw_item.get("unitPrice", 0))),
            "totalPrice": float(cw_item.get("total_price", cw_item.get("totalPrice", 0))),
            "price": float(cw_item.get("total_price", cw_item.get("totalPrice", 0))), # "price" e "totalPrice" parecem ser o mesmo no exemplo
            "observations": cw_item.get("observation", cw_item.get("observations", None)),
            "imageUrl": cw_item.get("image_url", None), # TODO: Mapear do CardapioWeb se disponível
            "options": options_details if options_details else None, # Enviar null se vazio, como no exemplo
            "index": cw_item.get("index", 1), # TODO: Se o CardapioWeb fornecer um índice/ordem
            "unit": "UN", # TODO: Mapear do CardapioWeb se disponível
            "ean": None, # TODO: Mapear do CardapioWeb se disponível
            "uniqueId": None, # TODO: Se CardapioWeb tiver um ID único de instância do item no pedido
            "optionsPrice": sum(opt.get("price", 0) for opt in options_details), # Soma dos preços das opções
            "addition": 0, # TODO: Verificar se este é o valor total de adicionais
            "scalePrices": None # TODO: Mapear do CardapioWeb se disponível
        })

    # PAYMENTS
    # A estrutura de payments do Consumer é complexa.
    # Você recebe cw.get("payments", []) do CardapioWeb. Precisa mapear isso.
    cw_payments = cw.get("payments", [])
    payment_methods_details = []
    total_prepaid = 0.0
    
    # Exemplo simplificado de mapeamento - ajuste conforme os dados reais do CardapioWeb
    for cw_pay_method in cw_payments:
        method_type = str(cw_pay_method.get("payment_method", "OTHER")).upper()
        # Mapear para os tipos do Consumer: DINHEIRO, CARTAO_CREDITO, CARTAO_DEBITO, PIX, VALE_REFEICAO, OUTRO
        # Exemplo: if method_type == "MONEY": method_type = "DINHEIRO"
        
        card_details = None
        if "card" in cw_pay_method: # ou outro indicador de pagamento com cartão
             card_details = { "brand": cw_pay_method.get("card", {}).get("brand", None) } # TODO: Mapear bandeira

        payment_methods_details.append({
            "method": method_type,
            "type": str(cw_pay_method.get("payment_type", "OFFLINE")).upper(), # OFFLINE, ONLINE
            "currency": "BRL", # Assumindo BRL
            "value": float(cw_pay_method.get("total", 0.0)), # Valor pago com este método
            "card": card_details,
            # "prepaid": bool(cw_pay_method.get("prepaid", False)) # A doc do Consumer tem 'prepaid' fora de 'methods'
            # "cash": null, # para troco
            # "wallet": null
        })
        if cw_pay_method.get("payment_type", "").upper() == "ONLINE" or cw_pay_method.get("prepaid"): # Exemplo
            total_prepaid += float(cw_pay_method.get("total", 0.0))

    order_total_amount = float(cw.get("total", 0.0))
    payments_details = {
        "methods": payment_methods_details,
        "pending": max(0, order_total_amount - total_prepaid), # Valor pendente
        "prepaid": total_prepaid
    }

    # TOTAL
    # Você precisará calcular/obter subTotal e deliveryFee dos dados do CardapioWeb
    # Se cw.get("total") já é o orderAmount (total final com taxas)
    # subTotal = orderAmount - deliveryFee (aproximadamente)
    # TODO: Ajustar estes cálculos com base nos dados reais do CardapioWeb
    # Exemplo:
    delivery_fee_from_cw = float(cw_delivery.get("fee", 0.0)) # Supondo que o CardapioWeb tenha 'fee' em delivery
    sub_total_from_cw = order_total_amount - delivery_fee_from_cw # Estimativa bruta

    total_details = {
        "subTotal": sub_total_from_cw, # Valor dos produtos
        "deliveryFee": delivery_fee_from_cw,
        "orderAmount": order_total_amount, # Valor total do pedido
        "benefits": float(cw.get("discount_amount", 0.0)), # TODO: Mapear descontos do CardapioWeb
        "additionalFees": float(cw.get("additional_fees", 0.0)) # TODO: Mapear taxas adicionais do CardapioWeb
    }
    
    # MERCHANT
    merchant_details = {
        "id": str(cw.get("merchant_id", CARDAPIOWEB_MERCHANT_ID)),
        "name": cw.get("merchant_name", "Restaurante Padrão")
    }

    # Objeto de pedido principal para o Consumer
    transformed_order = {
        "id": str(cw.get("id")),
        "displayId": str(cw.get("display_id", cw.get("id"))),
        "orderType": cw.get("order_type", "DELIVERY").upper(),
        "salesChannel": cw.get("sales_channel", "PARTNER").upper(), # PARTNER é um valor comum para integrações
        "orderTiming": cw.get("order_timing", "IMMEDIATE").upper(),
        "createdAt": cw.get("created_at", current_time_iso),
        "preparationStartDateTime": cw.get("preparation_start_time", current_time_iso), # TODO: Mapear do CardapioWeb
        "merchant": merchant_details,
        "total": total_details,
        "payments": payments_details,
        "customer": customer_details,
        "delivery": delivery_details,
        "items": items_details,
        "benefits": None, # TODO: Se houver benefícios no nível do pedido
        "picking": None, # TODO: Mapear do CardapioWeb se disponível (instruções de coleta)
        "extraInfo": None, # TODO: Mapear do CardapioWeb se disponível
        "additionalFees": None, # TODO: Se houver taxas adicionais no nível do pedido (diferente de total.additionalFees)
        "schedule": None, # TODO: Se for pedido agendado, mapear detalhes
        "indoor": None, # TODO: Detalhes para consumo no local
        "takeout": None, # TODO: Detalhes para retirada
        # "additionalInfometadata": null # Campo parece ser um typo na doc do Consumer, talvez "additionalInfoMetadata"
                                     # Ou apenas "metadata"
    }
    return remove_null_values(transformed_order)


# ----------- ROTAS DA API -----------
@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        "status": "OK",
        "service": "Consumer-CardapioWeb API Bridge",
        "timestamp": agora_iso(),
        "version": "2.6.0", # Estrutura de detalhes do pedido ajustada
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
    
    order_id_from_event = str(event_data.get("id") or event_data.get("order_id"))
    if not order_id_from_event or order_id_from_event == "None":
        print(f"[{log_timestamp}] [WEBHOOK_ERROR] 'id' ou 'order_id' ausente. Payload: {event_data}")
        return jsonify({"error": "'id' ou 'order_id' do pedido é obrigatório."}), 400

    print(f"[{log_timestamp}] [WEBHOOK_RECEIVED] Evento para order_id: {order_id_from_event}")
    order_details_payload_from_cardapioweb = event_data # Payload original do webhook
    is_simple_notification = "order_id" in event_data and len(event_data.keys()) <= 6 

    if is_simple_notification:
        url = f"{CARDAPIOWEB_BASE_URL}/orders/{order_id_from_event}"
        headers = {"X-API-KEY": CARDAPIOWEB_API_KEY, "Content-Type": "application/json"}
        params = {"merchant_id": CARDAPIOWEB_MERCHANT_ID}
        print(f"[{log_timestamp}] [WEBHOOK_FETCH] Buscando detalhes pedido {order_id_from_event}")
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            order_details_payload_from_cardapioweb = response.json() # Agora contém todos os detalhes do CardapioWeb
            print(f"[{log_timestamp}] [WEBHOOK_FETCH_SUCCESS] Detalhes obtidos para {order_id_from_event}")
        except requests.exceptions.RequestException as e:
            print(f"[{log_timestamp}] [WEBHOOK_FETCH_ERROR] Falha ao buscar {order_id_from_event}: {e}\n{traceback.format_exc()}")
            return jsonify({"error": f"Erro ao buscar detalhes no CardapioWeb: {str(e)}"}), 502

    try:
        # Armazenamos o payload COMPLETO do CardapioWeb. A transformação para o formato Consumer
        # será feita quando o endpoint de detalhes do pedido for chamado.
        PEDIDOS_PENDENTES[order_id_from_event] = order_details_payload_from_cardapioweb
        
        print(f"[{log_timestamp}] [WEBHOOK_SUCCESS] Pedido {order_id_from_event} (payload CardapioWeb) armazenado. Pendentes: {len(PEDIDOS_PENDENTES)}")
        return jsonify({
            "success": True,
            "orderId": order_id_from_event,
            "message": "Pedido recebido e dados brutos armazenados."
        }), 200
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
        for order_key, order_payload_from_cardapioweb in list(PEDIDOS_PENDENTES.items()): 
            # Para o polling, precisamos apenas de alguns campos básicos do payload original do CardapioWeb
            # ou do objeto já transformado se você preferir manter uma única estrutura interna.
            # Por simplicidade e para seguir a especificação do Consumer, vamos pegar do payload original do CardapioWeb.
            items_para_polling.append({
                "id":       str(order_payload_from_cardapioweb.get("id")),
                "orderId":  str(order_payload_from_cardapioweb.get("id")), # Ou um campo orderId específico se houver
                "createdAt": order_payload_from_cardapioweb.get("created_at", log_timestamp), 
                "fullCode": "PLACED", # Assumindo que todos os pedidos novos estão como PLACED
                "code":     "PLC"
            })
        
        print(f"[{log_timestamp}] [POLLING_SUCCESS] Retornando {len(items_para_polling)} pedidos no formato resumido.")
        return jsonify({
            "items": items_para_polling,
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
        # Pega o payload original do CardapioWeb que foi armazenado
        pedido_payload_cardapioweb = PEDIDOS_PENDENTES.get(order_id_str)
        is_from_pending = True
        
        if not pedido_payload_cardapioweb:
            pedido_payload_cardapioweb = PEDIDOS_PROCESSADOS.get(order_id_str)
            is_from_pending = False

        if not pedido_payload_cardapioweb:
            print(f"[{log_timestamp}] [GET_ORDER_NOT_FOUND] Payload original do pedido {order_id_str} não encontrado.")
            return jsonify({"error": "Pedido não encontrado.", "item": None, "statusCode": 404, "reasonPhrase": "Not Found"}), 404

        # Transforma o payload do CardapioWeb para o formato de detalhes do Consumer AQUI
        pedido_detalhado_consumer_format = transform_order_data_for_consumer_details(pedido_payload_cardapioweb)
        
        print(f"[{log_timestamp}] [GET_ORDER_SUCCESS] Retornando detalhes do pedido {order_id_str} no formato Consumer.")
        
        if is_from_pending and order_id_str in PEDIDOS_PENDENTES:
            # Move o payload original para processados
            PEDIDOS_PROCESSADOS[order_id_str] = PEDIDOS_PENDENTES.pop(order_id_str)
            print(f"[{log_timestamp}] [GET_ORDER_MOVE] Payload original do pedido {order_id_str} movido para processados.")

        response_data = {
            "item": pedido_detalhado_consumer_format,
            "statusCode": 0,
            "reasonPhrase": None 
        }
        return jsonify(response_data), 200
        
    except Exception as e:
        print(f"[{log_timestamp}] [GET_ORDER_ERROR] Erro ao buscar/transformar {order_id}: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "Erro interno ao buscar detalhes do pedido.", "item": None, "statusCode": 500, "reasonPhrase": "Internal Server Error"}), 500

@app.route('/api/parceiro/orders/<string:order_id>/status', methods=['POST'])
def update_order_status(order_id):
    log_timestamp = agora_iso()
    if not verify_token(request):
        return jsonify({"error": "Token de autenticação inválido ou ausente."}), 401
    
    data = request.get_json(silent=True)
    if not data:
        print(f"[{log_timestamp}] [UPDATE_STATUS_ERROR] Payload JSON inválido para {order_id}.")
        return jsonify({"error": "Payload JSON obrigatório."}), 400

    new_status_from_consumer = data.get("status") # Ex: CONFIRMED, DISPATCHED, CONCLUDED, CANCELED
    # A documentação do Consumer não detalha o payload de atualização de status enviado por eles.
    # Assumindo que eles enviam pelo menos 'status'. Podem enviar 'code' e 'fullCode' também.
    
    if not new_status_from_consumer:
        print(f"[{log_timestamp}] [UPDATE_STATUS_ERROR] Campo 'status' ausente para {order_id}.")
        return jsonify({"error": "Campo 'status' é obrigatório."}), 400

    try:
        order_id_str = str(order_id)
        # O status é atualizado no payload original do CardapioWeb armazenado,
        # pois não temos uma representação separada "Consumer format" persistida.
        # Se necessário, poderíamos adicionar campos de status Consumer-specific.
        pedido_payload_cardapioweb = PEDIDOS_PENDENTES.get(order_id_str)
        dict_source = PEDIDOS_PENDENTES
        
        if not pedido_payload_cardapioweb:
            pedido_payload_cardapioweb = PEDIDOS_PROCESSADOS.get(order_id_str)
            dict_source = PEDIDOS_PROCESSADOS

        if not pedido_payload_cardapioweb:
            print(f"[{log_timestamp}] [UPDATE_STATUS_NOT_FOUND] Pedido {order_id_str} não encontrado.")
            return jsonify({"error": "Pedido não encontrado para atualização."}), 404

        # Atualizar campos relacionados ao status no payload armazenado (que é do CardapioWeb)
        # O Consumer espera uma resposta simples de sucesso, não necessariamente o pedido atualizado.
        # Mas é bom manter nosso estado interno consistente.
        # Se o CardapioWeb tiver campos de status, você poderia mapeá-los aqui também.
        # Por agora, apenas logamos e respondemos sucesso.
        
        # Exemplo de como você poderia guardar o status do Consumer se quisesse:
        # pedido_payload_cardapioweb['_consumer_status_info'] = {
        #     "status": new_status_from_consumer,
        #     "fullCode": data.get("fullCode", new_status_from_consumer.upper()),
        #     "code": data.get("code", new_status_from_consumer[:3].upper()),
        #     "updatedAt": log_timestamp
        # }
        # dict_source[order_id_str] = pedido_payload_cardapioweb # Salva de volta

        print(f"[{log_timestamp}] [UPDATE_STATUS_RECEIVED] Status do pedido {order_id_str} recebido do Consumer: {new_status_from_consumer}.")
        
        # TODO: Aqui você pode adicionar lógica para propagar essa mudança de status para o CardapioWeb se necessário.

        # Mover para processados se o status for final e ainda estiver em pendentes
        if order_id_str in PEDIDOS_PENDENTES and \
           new_status_from_consumer.upper() in ["CONFIRMED", "DISPATCHED", "DELIVERED", "CONCLUDED", "CANCELLED", "CANCELED"]:
             PEDIDOS_PROCESSADOS[order_id_str] = PEDIDOS_PENDENTES.pop(order_id_str)
             print(f"[{log_timestamp}] [UPDATE_STATUS_MOVE] Payload original do pedido {order_id_str} movido para processados após status: {new_status_from_consumer}.")
        
        return jsonify({ # A documentação do Consumer não especifica a resposta para este endpoint. Sucesso simples é comum.
            "success": True,
            "message": f"Status do pedido {order_id_str} atualizado para {new_status_from_consumer}."
        }), 200
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
        # "dados_pedidos_pendentes": PEDIDOS_PENDENTES, # Descomente para ver dados completos (cuidado com o tamanho)
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
    return jsonify(error="Requisição inválida.", details=desc), 400

@app.errorhandler(401)
def handle_unauthorized(e): 
    log_timestamp = agora_iso()
    desc = e.description if hasattr(e, 'description') else "Token de autenticação inválido ou ausente."
    print(f"[{log_timestamp}] [ERROR_HANDLER_401] Unauthorized access attempt. Description: {desc}")
    return jsonify(error="Não autorizado.", details=desc), 401

@app.errorhandler(404)
def handle_not_found(e):
    log_timestamp = agora_iso()
    desc = e.description if hasattr(e, 'description') else str(e)
    print(f"[{log_timestamp}] [ERROR_404] Not Found: {request.path}. Details: {desc}")
    return jsonify(error="Recurso não encontrado.", endpoint=request.path), 404

@app.errorhandler(500)
def handle_internal_server_error(e_internal): # Renomeado para evitar conflito com 'e' de handle_generic_exception 
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [ERROR_HANDLER_500] Internal Server Error: {e_internal}\n{traceback.format_exc()}")
    return jsonify(error="Erro interno do servidor.", details=str(e_internal)), 500

@app.errorhandler(Exception) 
def handle_generic_exception(e_generic): # Renomeado para evitar conflito
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [ERROR_HANDLER_GENERIC] Unhandled Exception: {e_generic}\n{traceback.format_exc()}")
    if hasattr(e_generic, 'code') and isinstance(e_generic.code, int) and 400 <= e_generic.code < 600: 
        return jsonify(error=str(e_generic.name if hasattr(e_generic, 'name') else type(e_generic).__name__), 
                       details=str(e_generic.description if hasattr(e_generic, 'description') else e_generic)), e_generic.code
    return jsonify(error="Erro inesperado no servidor."), 500

# ----------- EXECUÇÃO -----------
if __name__ == '__main__':
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [STARTUP] Iniciando Consumer-CardapioWeb API Bridge v2.6.0 (Dev Mode)")
    app.run(debug=False, host='0.0.0.0', port=8080)
