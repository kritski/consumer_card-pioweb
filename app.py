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
CARDAPIOWEB_BASE_URL = 'https://integracao.cardapioweb.com/api/partner/v1' # Usado para buscar detalhes se o webhook for simples
CARDAPIOWEB_API_KEY = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5' # Usado para buscar detalhes
CARDAPIOWEB_MERCHANT_ID = '14104' # Usado para buscar detalhes

# O token que o Consumer usa para se autenticar na SUA API
CONSUMER_API_TOKEN = 'pk_live_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy'

PEDIDOS_PENDENTES = {} # Armazena o payload original do CardapioWeb
PEDIDOS_PROCESSADOS = {} # Armazena o payload original do CardapioWeb

# ----------- FUNÇÕES AUXILIARES -----------
def agora_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def _remove_null_values_recursive(obj_data):
    if isinstance(obj_data, dict):
        return {k: _remove_null_values_recursive(v) for k, v in obj_data.items() if v is not None}
    elif isinstance(obj_data, list):
        new_list = [_remove_null_values_recursive(i) for i in obj_data]
        return [item for item in new_list if item is not None] # Remove None da lista também
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
    """Tenta formatar um timestamp para o formato ISO com Z, se não estiver já."""
    if not timestamp_str:
        return None
    try:
        # Tenta converter se for um formato conhecido que não seja ISO com Z
        # Exemplo: se CardapioWeb envia "YYYY-MM-DD HH:MM:SS"
        # dt_obj = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        # return dt_obj.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

        # Se já vier no formato ISO mas sem o Z final, ou com offset diferente
        dt_obj = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        return dt_obj.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    except ValueError:
        # Se já estiver no formato correto ou for irreconhecível, retorna como está ou None
        if isinstance(timestamp_str, str) and timestamp_str.endswith('Z'):
            return timestamp_str
        print(f"[WARN] Timestamp '{timestamp_str}' não pôde ser normalizado para o formato ISO com Z.")
        return timestamp_str # Ou None, dependendo da criticidade

def transform_order_data_for_consumer_details(cw_payload):
    """
    Transforma o payload do pedido do CardapioWeb (cw_payload) para a estrutura DETALHADA
    esperada pelo endpoint de detalhes do pedido do Consumer.
    """
    current_time_iso = agora_iso()

    # CUSTOMER
    # Documentação CardapioWeb (baseado no GET Orders do Postman):
    # pedido.Usuario: { nome, sobrenome, cpf, email, genero, telefone (não detalhado) }
    # Ou, se o webhook for mais rico (como no seu transform_order_data anterior):
    # cw_payload.customer: { id, name, phone: {number} }
    
    cw_customer_data = cw_payload.get("customer", cw_payload.get("Usuario", {})) # Tenta ambos os padrões
    
    phone_number_from_cw = None
    if isinstance(cw_customer_data.get("phone"), dict):
        phone_number_from_cw = cw_customer_data.get("phone", {}).get("number")
    elif isinstance(cw_customer_data.get("telefone"), str): # Do GET Orders
         phone_number_from_cw = cw_customer_data.get("telefone")
    elif isinstance(cw_customer_data.get("phone"), str): # Do seu transformador antigo
         phone_number_from_cw = cw_customer_data.get("phone")


    customer_name_from_cw = cw_customer_data.get("name") # Do seu transformador antigo
    if not customer_name_from_cw and cw_customer_data.get("nome"): # Do GET Orders
        customer_name_from_cw = f"{cw_customer_data.get('nome', '')} {cw_customer_data.get('sobrenome', '')}".strip()

    customer_details = {
        "id": str(cw_customer_data.get("id", "")), # Consumer quer string. CardapioWeb pode ter int.
        "name": customer_name_from_cw if customer_name_from_cw else "Cliente não informado",
        "phone": {
            "number": phone_number_from_cw if phone_number_from_cw else "",
            "localizer": None, # Não parece ter no CardapioWeb
            "localizerExpiration": None # Não parece ter no CardapioWeb
        },
        "documentNumber": cw_customer_data.get("cpf", cw_customer_data.get("document", None)) # Consumer: documentNumber, CardapioWeb: cpf
    }

    # DELIVERY
    # Documentação CardapioWeb (baseado no GET Orders do Postman):
    # pedido.Entrega: { endereco, numero, complemento, bairro, cidade, estado, cep, obs } (obs aqui é da entrega)
    # Ou, se o webhook for mais rico (como no seu transform_order_data anterior):
    # cw_payload.delivery: { mode, deliveredBy, deliveryAddress: { streetName, number, ...}, deliveryDateTime, pickupCode }
    
    cw_delivery_data = cw_payload.get("delivery", cw_payload.get("Entrega", {})) # Tenta ambos
    
    # Se cw_delivery_data for o objeto "Entrega" do CardapioWeb, o endereço está direto nele.
    # Se for o objeto "delivery" do seu payload original, o endereço está em "deliveryAddress".
    cw_address_data = cw_delivery_data.get("deliveryAddress", cw_delivery_data)


    delivery_details = {
        "mode": cw_delivery_data.get("mode", "DEFAULT").upper(), # Consumer: DEFAULT. CardapioWeb: não claro, assumir padrão.
        "deliveredBy": cw_delivery_data.get("deliveredBy", "PARTNER").capitalize(), # Consumer: Partner ou Merchant
        "pickupCode": cw_delivery_data.get("pickupCode", None),
        "deliveryDateTime": format_timestamp_for_consumer(cw_delivery_data.get("deliveryDateTime", cw_payload.get("transito_em", cw_payload.get("createdAt", current_time_iso)))), # Consumer: deliveryDateTime. CardapioWeb: transito_em (saiu para entrega) ou aceito_em
        "deliveryAddress": {
            "country": cw_address_data.get("country", "BR"),
            "state": cw_address_data.get("estado", cw_address_data.get("state", "")), # Consumer: state. CardapioWeb: estado
            "city": cw_address_data.get("cidade", cw_address_data.get("city", "")), # Consumer: city. CardapioWeb: cidade
            "postalCode": cw_address_data.get("cep", cw_address_data.get("postalCode", "")),# Consumer: postalCode. CardapioWeb: cep
            "streetName": cw_address_data.get("endereco", cw_address_data.get("streetName", "")),# Consumer: streetName. CardapioWeb: endereco
            "streetNumber": cw_address_data.get("numero", cw_address_data.get("streetNumber", "")),# Consumer: streetNumber. CardapioWeb: numero
            "neighborhood": cw_address_data.get("bairro", cw_address_data.get("neighborhood", "")),# Consumer: neighborhood. CardapioWeb: bairro
            "complement": cw_address_data.get("complemento", None), # Consumer: complement. CardapioWeb: complemento
            "reference": cw_address_data.get("reference", None), # Consumer: reference
            "formattedAddress": None, # TODO: YURI REVISAR: Montar a partir dos campos acima se necessário, ou verificar se CardapioWeb fornece.
            "coordinates": None     # TODO: YURI REVISAR: Mapear {latitude, longitude} se CardapioWeb fornecer.
        },
        "observations": cw_delivery_data.get("obs", None) # Consumer: observations. CardapioWeb (Entrega): obs
    }

    # ITEMS e OPTIONS
    # Documentação CardapioWeb (baseado no GET Orders do Postman):
    # pedido.Itens[]: { id_produto, nome_produto, quantidade, valor_unitario, valor_total, obs, Complementos[] }
    # pedido.Itens[].Complementos[]: { id_complemento, nome_complemento, quantidade, valor }
    # Ou, se o webhook for mais rico (como no seu transform_order_data anterior):
    # cw_payload.items[]: { id, externalCode, name, quantity, unitPrice, totalPrice, observations, options[] }
    # cw_payload.items[].options[]: { optionId, externalCode, name, optionGroupId, optionGroupName, quantity, unitPrice }

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
                    "name": option_name,
                    "quantity": option_qty,
                    "unitPrice": option_unit_price,
                    "price": option_unit_price * option_qty, # Total da opção
                    "externalCode": cw_opt_data.get("externalCode", None), # TODO: YURI REVISAR: Mapear do CardapioWeb se houver código externo para opção/complemento
                    "unit": "UN", # TODO: YURI REVISAR: CardapioWeb fornece unidade para opção?
                    "ean": None,  # TODO: YURI REVISAR: CardapioWeb fornece EAN para opção?
                    "index": opt_index, # Índice da opção dentro do item
                    "addition": 0 # TODO: YURI REVISAR: Se a opção é um adicional e tem um valor específico de "adição"
                })
        
        item_unit_price = float(cw_item_data.get("unitPrice", cw_item_data.get("valor_unitario", 0.0)))
        item_quantity = int(cw_item_data.get("quantity", 1))
        # totalPrice no Consumer é unitPrice * quantity do item principal, sem opções.
        # 'price' no item do Consumer no exemplo parece ser o totalPrice do item.
        item_total_price = item_unit_price * item_quantity
        
        # optionsPrice: soma dos preços totais das opções (price de cada opção)
        item_options_price = sum(opt.get("price", 0.0) for opt in options_for_consumer)


        items_details_for_consumer.append({
            "id": str(cw_item_data.get("id", cw_item_data.get("id_produto", ""))),
            "externalCode": cw_item_data.get("externalCode", None), # TODO: YURI REVISAR: Mapear do CardapioWeb se houver um código externo geral do produto
            "name": cw_item_data.get("name", cw_item_data.get("nome_produto", "")),
            "quantity": item_quantity,
            "unitPrice": item_unit_price,
            "totalPrice": item_total_price, # unitPrice * quantity do item principal
            "price": item_total_price, # Conforme exemplo do Consumer, parece ser o mesmo que totalPrice do item
            "observations": cw_item_data.get("observations", cw_item_data.get("obs", None)),
            "imageUrl": cw_item_data.get("imageUrl", None), # TODO: YURI REVISAR: CardapioWeb fornece URL de imagem para o item?
            "options": options_for_consumer if options_for_consumer else None,
            "index": item_index + 1, # Consumer usa index 1-based
            "unit": "UN", # TODO: YURI REVISAR: CardapioWeb fornece unidade para o item?
            "ean": None,  # TODO: YURI REVISAR: CardapioWeb fornece EAN para o item?
            "uniqueId": None, # TODO: YURI REVISAR: CardapioWeb tem um uniqueId para a instância do item no pedido?
            "optionsPrice": item_options_price, # Soma dos preços das opções
            "addition": 0, # TODO: YURI REVISAR: Este campo se refere ao valor total de adicionais? Ou é por opção?
            "scalePrices": None # TODO: YURI REVISAR: Mapear se CardapioWeb tiver preços por escala
        })

    # PAYMENTS
    cw_payments_list = cw_payload.get("payments", []) # Do seu transformador antigo
    # CardapioWeb GET Orders não detalha pagamentos, mas seu webhook pode ter.
    
    payment_methods_for_consumer = []
    total_order_amount_from_payments = 0.0

    for cw_pay_method in cw_payments_list: # Assumindo que cw_payments_list é uma lista de dicts
        method_type_consumer = str(cw_pay_method.get("payment_method", "OTHER")).upper()
        # TODO: YURI REVISAR: Mapear os "payment_method" do CardapioWeb para os valores aceitos pelo Consumer
        # (CREDIT, DEBIT, MEAL_VOUCHER, PIX, CASH, BANK_TRANSFER, OTHER)
        # Exemplo: if method_type_consumer == "MONEY": method_type_consumer = "CASH"
        
        card_details_consumer = None
        if "card" in cw_pay_method or "card_brand" in cw_pay_method: # Checar como CardapioWeb envia info de cartão
            card_brand_from_cw = None
            if isinstance(cw_pay_method.get("card"), dict):
                card_brand_from_cw = cw_pay_method.get("card", {}).get("brand")
            elif isinstance(cw_pay_method.get("card_brand"), str):
                card_brand_from_cw = cw_pay_method.get("card_brand")
            
            card_details_consumer = { "brand": card_brand_from_cw } if card_brand_from_cw else None

        payment_value = float(cw_pay_method.get("total", cw_pay_method.get("value", 0.0)))
        total_order_amount_from_payments += payment_value

        payment_methods_for_consumer.append({
            "method": method_type_consumer,
            "type": str(cw_pay_method.get("payment_type", "OFFLINE")).upper(), # OFFLINE, ONLINE
            "currency": "BRL",
            "value": payment_value,
            "card": card_details_consumer
            # "cash": null, "wallet": null - Não mapeados por enquanto
        })

    # O `total.orderAmount` deve ser a soma dos `value` em `payments.methods` OU o total geral do pedido.
    # A documentação do Consumer é um pouco ambígua se `payments.pending` e `payments.prepaid`
    # são calculados a partir dos methods ou vêm separados.
    # Vamos assumir que `cw_payload.get("total")` é o valor final do pedido.
    final_order_total = float(cw_payload.get("total", 0.0))
    
    # Estimativa de prepaid: se algum payment method for online.
    # TODO: YURI REVISAR: Lógica mais precisa para prepaid/pending baseada nos dados do CardapioWeb
    prepaid_amount_calculated = 0.0
    for pm in payment_methods_for_consumer:
        if pm.get("type") == "ONLINE":
            prepaid_amount_calculated += pm.get("value", 0.0)
            
    pending_amount_calculated = max(0, final_order_total - prepaid_amount_calculated)


    payments_for_consumer = {
        "methods": payment_methods_for_consumer,
        "pending": pending_amount_calculated,
        "prepaid": prepaid_amount_calculated
    }

    # TOTAL
    # Documentação Consumer: total.benefits, total.deliveryFee, total.orderAmount, total.subTotal, total.additionalFees
    # CardapioWeb (seu transformador): cw_payload.get("total") é um float.
    # CardapioWeb `total` (Float - Total da compra sem descontos)
    # Precisamos de mais informações do CardapioWeb para `deliveryFee`, `benefits`, `additionalFees` para calcular `subTotal`.

    # TODO: YURI REVISAR: Obter estes valores do payload do CardapioWeb
    cw_delivery_fee = float(cw_payload.get("delivery_fee", 0.0)) # Suposição de nome de campo
    cw_benefits_or_discount = float(cw_payload.get("discount_amount", 0.0)) # Suposição
    cw_additional_fees = float(cw_payload.get("additional_fees", 0.0)) # Suposição

    # subTotal = orderAmount - deliveryFee - additionalFees + benefits (se benefits for positivo como desconto)
    # Ou, se CardapioWeb.total já for o subtotal (sem taxas/descontos), então:
    # orderAmount = CardapioWeb.total + deliveryFee + additionalFees - benefits
    
    # Vamos assumir que `final_order_total` (de cw_payload.get("total")) é o `orderAmount` do Consumer.
    # E que `cw_payload.get("subtotal_items")` (suposição) seja o subtotal dos itens.
    
    sub_total_for_consumer = final_order_total - cw_delivery_fee - cw_additional_fees + cw_benefits_or_discount # Isso é uma estimativa
    # TODO: YURI REVISAR: A forma mais segura é se o CardapioWeb fornecer o subTotal dos itens separadamente.
    # Se cw_payload.get("total") do CardapioWeb for o total dos itens (subTotal), então:
    # sub_total_for_consumer = float(cw_payload.get("total", 0.0))
    # final_order_total = sub_total_for_consumer + cw_delivery_fee + cw_additional_fees - cw_benefits_or_discount


    total_for_consumer = {
        "subTotal": sub_total_for_consumer,
        "deliveryFee": cw_delivery_fee,
        "orderAmount": final_order_total,
        "benefits": cw_benefits_or_discount,
        "additionalFees": cw_additional_fees
    }
    
    # MERCHANT
    merchant_for_consumer = {
        "id": str(cw_payload.get("merchant_id", CARDAPIOWEB_MERCHANT_ID)), # Consumer espera string
        "name": cw_payload.get("merchant_name", "Restaurante Padrão")
    }

    # Objeto de pedido principal para o Consumer ("item")
    order_item_for_consumer = {
        "id": str(cw_payload.get("id")), # Consumer espera string
        "displayId": str(cw_payload.get("display_id", cw_payload.get("ref", cw_payload.get("id")))), # CardapioWeb: ref
        "orderType": cw_payload.get("order_type", "DELIVERY").upper(), # Ex: DELIVERY, TAKEOUT, INDOOR
        "salesChannel": cw_payload.get("sales_channel", "PARTNER").upper(), # Ex: IFOOD, RAPPI, PARTNER (para API própria)
        "orderTiming": cw_payload.get("order_timing", "IMMEDIATE").upper(), # Ex: IMMEDIATE, SCHEDULED
        "createdAt": format_timestamp_for_consumer(cw_payload.get("created_at", cw_payload.get("aceito_em", current_time_iso))), # CardapioWeb: aceito_em
        "preparationStartDateTime": format_timestamp_for_consumer(cw_payload.get("preparation_start_time", cw_payload.get("producao_em", current_time_iso))), # Consumer: preparationStartDateTime. CardapioWeb: producao_em
        
        "merchant": merchant_for_consumer,
        "total": total_for_consumer,
        "payments": payments_for_consumer,
        "customer": customer_details,
        "delivery": delivery_details,
        "items": items_details_for_consumer,

        # Campos opcionais no nível raiz do 'item' do Consumer
        "benefits": None, # TODO: YURI REVISAR: Se houver um campo de benefício/desconto geral do pedido no CardapioWeb
        "picking": None,  # TODO: YURI REVISAR: Instruções de coleta/retirada se CardapioWeb fornecer
        "extraInfo": None, # TODO: YURI REVISAR: Mapear se CardapioWeb tiver um campo de informações extras gerais
        "schedule": None,  # TODO: YURI REVISAR: Se orderTiming for SCHEDULED, preencher com dados de agendamento
                           # Ex: { "scheduledDateTimeStart": "...", "scheduledDateTimeEnd": "..." }
        "indoor": None,    # TODO: YURI REVISAR: Detalhes para consumo no local (mesa, etc.)
        "takeout": None,   # TODO: YURI REVISAR: Detalhes específicos de retirada se diferente de delivery.mode='TAKEOUT'
        # "additionalInfometadata": null # typo na doc Consumer?
    }
    
    # Os campos "status", "fullCode", "code" que você tinha no seu transformador antigo
    # não estão no exemplo de DETALHES do pedido do Consumer, mas sim no exemplo de POLLING.
    # Eles geralmente são gerenciados via endpoint de status.
    # No entanto, o objeto de pedido interno que você armazena pode ter esses campos,
    # mas o objeto retornado para DETALHES deve seguir a estrutura do Consumer.
    
    return remove_null_values(order_item_for_consumer)


# ----------- ROTAS DA API -----------
@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        "status": "OK",
        "service": "Consumer-CardapioWeb API Bridge",
        "timestamp": agora_iso(),
        "version": "2.6.1", # Mapeamento de detalhes do pedido aprimorado
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
        for order_key, order_payload_cw in list(PEDIDOS_PENDENTES.items()): 
            items_para_polling.append({
                "id":       str(order_payload_cw.get("id")),
                "orderId":  str(order_payload_cw.get("id")), 
                "createdAt": format_timestamp_for_consumer(order_payload_cw.get("created_at", order_payload_cw.get("aceito_em", log_timestamp))), 
                "fullCode": "PLACED", # Default para novos pedidos no polling
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
        pedido_payload_cardapioweb = PEDIDOS_PENDENTES.get(order_id_str)
        is_from_pending = True
        
        if not pedido_payload_cardapioweb:
            pedido_payload_cardapioweb = PEDIDOS_PROCESSADOS.get(order_id_str)
            is_from_pending = False

        if not pedido_payload_cardapioweb:
            print(f"[{log_timestamp}] [GET_ORDER_NOT_FOUND] Payload original do pedido {order_id_str} não encontrado.")
            return jsonify({"item": None, "statusCode": 1, "reasonPhrase": "Pedido não encontrado"}), 404 # Consumer espera 'item', 'statusCode', 'reasonPhrase'

        pedido_detalhado_consumer_format = transform_order_data_for_consumer_details(pedido_payload_cardapioweb)
        
        print(f"[{log_timestamp}] [GET_ORDER_SUCCESS] Retornando detalhes do pedido {order_id_str} no formato Consumer.")
        
        if is_from_pending and order_id_str in PEDIDOS_PENDENTES:
            PEDIDOS_PROCESSADOS[order_id_str] = PEDIDOS_PENDENTES.pop(order_id_str)
            print(f"[{log_timestamp}] [GET_ORDER_MOVE] Payload original do pedido {order_id_str} movido para processados.")

        response_data = {
            "item": pedido_detalhado_consumer_format,
            "statusCode": 0,
            "reasonPhrase": None # Ou "OK"
        }
        return jsonify(response_data), 200
        
    except Exception as e:
        print(f"[{log_timestamp}] [GET_ORDER_ERROR] Erro ao buscar/transformar {order_id}: {e}\n{traceback.format_exc()}")
        return jsonify({"item": None, "error": "Erro interno ao buscar detalhes do pedido.", "details": str(e), "statusCode": 2, "reasonPhrase": "Internal Server Error"}), 500 # Consumer espera 'statusCode', 'reasonPhrase'

@app.route('/api/parceiro/orders/<string:order_id>/status', methods=['POST'])
def update_order_status(order_id):
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
        
        if not pedido_payload_cardapioweb:
            pedido_payload_cardapioweb = PEDIDOS_PROCESSADOS.get(order_id_str)

        if not pedido_payload_cardapioweb:
            print(f"[{log_timestamp}] [UPDATE_STATUS_NOT_FOUND] Pedido {order_id_str} não encontrado.")
            return jsonify({"error": "Pedido não encontrado para atualização."}), 404
        
        print(f"[{log_timestamp}] [UPDATE_STATUS_RECEIVED] Status do pedido {order_id_str} recebido do Consumer: {new_status_from_consumer}. Payload: {data}")
        
        # TODO: YURI REVISAR: Aqui você precisaria mapear o status do Consumer para um status do CardapioWeb
        # e, idealmente, chamar a API do CardapioWeb para atualizar o status lá também.
        # Exemplo: if new_status_from_consumer == "CONFIRMED": ... chamar API CardapioWeb ...

        # Atualizar o status no nosso armazenamento interno (no payload original do CardapioWeb)
        # Adicionar um campo customizado para não sobrescrever um campo de status original do CardapioWeb sem querer
        if '_consumer_integration_status' not in pedido_payload_cardapioweb:
            pedido_payload_cardapioweb['_consumer_integration_status'] = {}
        
        pedido_payload_cardapioweb['_consumer_integration_status']['status'] = new_status_from_consumer
        pedido_payload_cardapioweb['_consumer_integration_status']['fullCode'] = data.get("fullCode", new_status_from_consumer.upper())
        pedido_payload_cardapioweb['_consumer_integration_status']['code'] = data.get("code", new_status_from_consumer[:3].upper())
        pedido_payload_cardapioweb['_consumer_integration_status']['updatedAt'] = log_timestamp
        
        # Salvar de volta no dicionário de onde veio
        if order_id_str in PEDIDOS_PENDENTES:
            PEDIDOS_PENDENTES[order_id_str] = pedido_payload_cardapioweb
            if new_status_from_consumer.upper() in ["CONFIRMED", "DISPATCHED", "DELIVERED", "CONCLUDED", "CANCELLED", "CANCELED"]:
                 PEDIDOS_PROCESSADOS[order_id_str] = PEDIDOS_PENDENTES.pop(order_id_str)
                 print(f"[{log_timestamp}] [UPDATE_STATUS_MOVE] Payload do pedido {order_id_str} movido para processados.")
        elif order_id_str in PEDIDOS_PROCESSADOS:
            PEDIDOS_PROCESSADOS[order_id_str] = pedido_payload_cardapioweb
        
        # A documentação do Consumer não especifica o corpo da resposta para este endpoint.
        # Um JSON simples de sucesso é uma boa prática.
        return jsonify({
            "message": "Status do pedido recebido com sucesso.", # Ou um objeto de sucesso mais detalhado se o Consumer esperar
            "orderId": order_id_str,
            "newStatus": new_status_from_consumer 
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
    # ... (mantido)
    log_timestamp = agora_iso()
    desc = e.description if hasattr(e, 'description') else str(e)
    print(f"[{log_timestamp}] [ERROR_400] Bad Request: {desc}")
    return jsonify(error="Requisição inválida.", details=desc, statusCode=3, reasonPhrase="Bad Request"), 400 # Adicionando statusCode/reasonPhrase

@app.errorhandler(401)
def handle_unauthorized(e): 
    # ... (mantido)
    log_timestamp = agora_iso()
    desc = e.description if hasattr(e, 'description') else "Token de autenticação inválido ou ausente."
    print(f"[{log_timestamp}] [ERROR_HANDLER_401] Unauthorized access attempt. Description: {desc}")
    return jsonify(error="Não autorizado.", details=desc, statusCode=4, reasonPhrase="Unauthorized"), 401

@app.errorhandler(404)
def handle_not_found(e):
    # ... (mantido)
    log_timestamp = agora_iso()
    desc = e.description if hasattr(e, 'description') else str(e)
    print(f"[{log_timestamp}] [ERROR_404] Not Found: {request.path}. Details: {desc}") # Loga o path que deu 404
    # A rota get_order_details já retorna um formato específico para 404, este é um fallback.
    return jsonify(error="Recurso não encontrado.", endpoint=request.path, statusCode=1, reasonPhrase="Not Found"), 404


@app.errorhandler(500)
def handle_internal_server_error(e_internal): 
    # ... (mantido)
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [ERROR_HANDLER_500] Internal Server Error: {e_internal}\n{traceback.format_exc()}")
    return jsonify(error="Erro interno do servidor.", details=str(e_internal), statusCode=2, reasonPhrase="Internal Server Error"), 500

@app.errorhandler(Exception) 
def handle_generic_exception(e_generic): 
    # ... (mantido)
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [ERROR_HANDLER_GENERIC] Unhandled Exception: {e_generic}\n{traceback.format_exc()}")
    if hasattr(e_generic, 'code') and isinstance(e_generic.code, int) and 400 <= e_generic.code < 600: 
        return jsonify(error=str(e_generic.name if hasattr(e_generic, 'name') else type(e_generic).__name__), 
                       details=str(e_generic.description if hasattr(e_generic, 'description') else e_generic), 
                       statusCode=5, reasonPhrase="Unhandled HTTP Exception"), e_generic.code # statusCode genérico
    return jsonify(error="Erro inesperado no servidor.", statusCode=2, reasonPhrase="Unexpected Server Error"), 500


# ----------- EXECUÇÃO -----------
if __name__ == '__main__':
    log_timestamp = agora_iso()
    print(f"[{log_timestamp}] [STARTUP] Iniciando Consumer-CardapioWeb API Bridge v2.6.1 (Dev Mode)")
    app.run(debug=False, host='0.0.0.0', port=8080)
