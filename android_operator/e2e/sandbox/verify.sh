#!/usr/bin/env bash
# End-to-end check of the sandbox API — prints the transcript pasted in VERIFY.md.
# Needs the API running (./run_api.sh start), curl, jq, python3.
# It RESETS the seed first (so the numbers match), then mutates state like the app would.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

B=http://127.0.0.1:8010
LAURA=wa_000000000101 SOFIA=wa_000000000102 CAMILO=wa_000000000103
ANDRES=wa_000000000104 VALENTINA=wa_000000000105 DANIELA=wa_000000000106 MATEO=wa_000000000107
ORDER_41=order_01SBXANDRES000000000000041
SSE_FILE="$PWD/sse_capture.txt"

hr() { printf '\n### %s\n' "$1"; }
cmd() { printf '$ %s\n' "$*"; }
# call METHOD PATH [JSON]  → prints "HTTP <code>" then the body (raw); body also in $BODY
call() {
  local method="$1" path="$2" data="${3:-}" out
  if [ -n "$data" ]; then
    out="$(curl -sS -X "$method" "$B$path" -H 'content-type: application/json' -d "$data" -w '\n%{http_code}')"
  else
    out="$(curl -sS -X "$method" "$B$path" -w '\n%{http_code}')"
  fi
  CODE="${out##*$'\n'}"; BODY="${out%$'\n'*}"
}

hr "0. reset the seed (timestamps re-based to now) + health"
cmd "python3 inject.py reset"; python3 inject.py reset
sleep 2
cmd "curl -s $B/"; curl -sS "$B/"; echo
cmd "curl -s $B/__sandbox/info | jq '{plugins_loaded, fakes}'"; curl -sS "$B/__sandbox/info" | jq -c '{plugins_loaded, fakes}'

# SSE listener in the background for the whole run (starts the real sampler).
TICKET="$(curl -sS -X POST "$B/api/dashboard/sse-ticket" | jq -r .ticket)"
( curl -sS -N --max-time 120 "$B/api/dashboard/events?ticket=$TICKET" >"$SSE_FILE" 2>/dev/null & )
sleep 3  # the real sampler takes its baseline 2.5 s after the first subscriber

hr "1. GET /api/dashboard/sessions (inbox)"
cmd "curl -s $B/api/dashboard/sessions | jq '.sessions[] | {session_id, tag, active_agent_route, unanswered_count, order_ref, pending_payment_order_id}'"
curl -sS "$B/api/dashboard/sessions" | jq -c '.sessions[] | {session_id, tag, active_agent_route, unanswered_count, order_ref, pending_payment_order_id}'

hr "2. GET /api/dashboard/sessions/$ANDRES (chat detail exposes the order ref → «Pedido #41»)"
cmd "curl -s $B/api/dashboard/sessions/$ANDRES | jq '{session_id, active_agent_route, order_ref, service_window_expires_at_ms, n_messages: (.messages|length), last_two: [.messages[-2:][] | {ui_type, content: .content[0:90]}]}'"
curl -sS "$B/api/dashboard/sessions/$ANDRES" | jq '{session_id, active_agent_route, order_ref, service_window_expires_at_ms, n_messages: (.messages|length), last_two: [.messages[-2:][] | {ui_type, content: .content[0:90]}]}'

hr "3. GET /api/chats/mobile/suggestions/$LAURA"
cmd "curl -s $B/api/chats/mobile/suggestions/$LAURA"
curl -sS "$B/api/chats/mobile/suggestions/$LAURA" | jq .

hr "4. GET /api/chats/mobile/fires (grave first)"
cmd "curl -s $B/api/chats/mobile/fires | jq '{decided_by, fires: [.fires[] | {fire_id, severity, kind, title, subtitle, getting_worse, primary_action}]}'"
curl -sS "$B/api/chats/mobile/fires" | jq '{decided_by, fires: [.fires[] | {fire_id, severity, kind, title, subtitle, getting_worse, primary_action}]}'

hr "5. GET /api/chats/mobile/hot"
cmd "curl -s $B/api/chats/mobile/hot"
curl -sS "$B/api/chats/mobile/hot" | jq .

hr "6. GET /api/chats/catalog"
cmd "curl -s $B/api/chats/catalog | jq -c '.products[]'"
curl -sS "$B/api/chats/catalog" | jq -c '.products[]'

hr "7. POST + DELETE /api/chats/mobile/devices"
cmd "curl -s -X POST $B/api/chats/mobile/devices -d '{\"token\":\"fcm:sandbox-demo\",\"platform\":\"android\",\"app_version\":\"0.1.0\"}'"
call POST /api/chats/mobile/devices '{"token":"fcm:sandbox-demo","platform":"android","app_version":"0.1.0"}'; echo "HTTP $CODE"
cmd "cat data/vault/_mobile/devices.json"; jq -c . data/vault/_mobile/devices.json
cmd "curl -s -X DELETE $B/api/chats/mobile/devices/fcm:sandbox-demo"
call DELETE /api/chats/mobile/devices/fcm:sandbox-demo; echo "HTTP $CODE"; jq -c . data/vault/_mobile/devices.json

hr "8. POST /api/dashboard/sessions/$LAURA/intervene"
cmd "curl -s -X POST $B/api/dashboard/sessions/$LAURA/intervene -d '{\"motivo\":\"El operador toma el chat desde la app\"}'"
call POST "/api/dashboard/sessions/$LAURA/intervene" '{"motivo":"El operador toma el chat desde la app"}'; echo "HTTP $CODE"; echo "$BODY" | jq -c .
cmd "curl -s $B/api/dashboard/sessions/$LAURA | jq '{active_agent_route, tag}'"
curl -sS "$B/api/dashboard/sessions/$LAURA" | jq -c '{active_agent_route, tag}'
cmd "tail -4 temporal.log   # the real terminate_session_workflows() against the fake Temporal"
tail -4 temporal.log

hr "9. POST /api/chats/session-actions/$LAURA/tools/present_variant_picker"
ARGS='{"client_action_id":"act-verify-aromas-1","args":{"product":"duo-zodiacal","attribute":"aroma"}}'
cmd "curl -s -X POST $B/api/chats/session-actions/$LAURA/tools/present_variant_picker -d '$ARGS'"
call POST "/api/chats/session-actions/$LAURA/tools/present_variant_picker" "$ARGS"; echo "HTTP $CODE"; echo "$BODY" | jq -c .
cmd "(same client_action_id again → nothing re-sent)"
call POST "/api/chats/session-actions/$LAURA/tools/present_variant_picker" "$ARGS"; echo "HTTP $CODE"; echo "$BODY" | jq -c .
cmd "tail -1 sent.log | jq '{via, to, type, wa_message_id, body: .payload.text.body}'"
tail -1 sent.log | jq '{via, to, type, wa_message_id, body: .payload.text.body}'
cmd "tail -1 data/vault/$LAURA/sessions/$LAURA.jsonl | jq '{role, sender, operator_tool, wamid, content}'"
tail -1 "data/vault/$LAURA/sessions/$LAURA.jsonl" | jq '{role, sender, operator_tool, wamid, content}'
cmd "curl -s $B/api/chats/mobile/suggestions/$LAURA | jq -c '{in_control, suggestions: [.suggestions[].id]}'   # the sent card is not suggested again"
curl -sS "$B/api/chats/mobile/suggestions/$LAURA" | jq -c '{in_control, suggestions: [.suggestions[].id]}'

hr "10. POST /api/dashboard/sessions/$LAURA/messages (operator free text)"
MSG='{"text":"¡Hola Laura! Soy Ana, del equipo 😊 ¿Cuál aroma te gusta para cada vela?","client_message_id":"cmid-verify-1"}'
cmd "curl -s -X POST $B/api/dashboard/sessions/$LAURA/messages -d '$MSG'"
call POST "/api/dashboard/sessions/$LAURA/messages" "$MSG"; echo "HTTP $CODE"; echo "$BODY" | jq -c .
cmd "tail -1 sent.log | jq -c '{via, to, type, body: .payload.text.body}'"
tail -1 sent.log | jq -c '{via, to, type, body: .payload.text.body}'
cmd "tail -1 data/vault/$LAURA/sessions/$LAURA.jsonl | jq -c '{role, sender, content}'"
tail -1 "data/vault/$LAURA/sessions/$LAURA.jsonl" | jq -c '{role, sender, content}'

hr "11. Valentina — 24 h window closed → templates"
cmd "curl -s $B/api/chats/mobile/suggestions/$VALENTINA | jq -c '{window_open, in_control, suggestions}'"
curl -sS "$B/api/chats/mobile/suggestions/$VALENTINA" | jq -c '{window_open, in_control, suggestions}'
cmd "curl -s $B/api/dashboard/sessions/$VALENTINA | jq -c '{active_agent_route, service_window_expires_at_ms}'"
curl -sS "$B/api/dashboard/sessions/$VALENTINA" | jq -c '{active_agent_route, service_window_expires_at_ms}'
cmd "curl -s $B/api/dashboard/whatsapp-templates | jq -c '.templates[] | {name, is_default, header_format, variables: [.variables[].name]}'"
curl -sS "$B/api/dashboard/whatsapp-templates" | jq -c '.templates[] | {name, is_default, header_format, variables: [.variables[].name]}'
cmd "curl -s -X POST $B/api/dashboard/sessions/$VALENTINA/messages -d '{\"text\":\"hola\",...}'   # free text is refused"
call POST "/api/dashboard/sessions/$VALENTINA/messages" '{"text":"hola","client_message_id":"cmid-verify-closed"}'; echo "HTTP $CODE"; echo "$BODY" | jq -c .
TPL='{"template_name":"human_followup_utility_v1","variables":{"followup_message":"sobre los centros de mesa para tu boda"},"client_message_id":"cmid-verify-tpl-1"}'
cmd "curl -s -X POST $B/api/dashboard/sessions/$VALENTINA/template-messages -d '$TPL'"
call POST "/api/dashboard/sessions/$VALENTINA/template-messages" "$TPL"; echo "HTTP $CODE"; echo "$BODY" | jq -c .
cmd "tail -1 sent.log | jq -c '{via, to, type, template: .payload.template.name, params: .payload.template.components}'"
tail -1 sent.log | jq -c '{via, to, type, template: .payload.template.name, params: .payload.template.components}'

hr "12. GET /api/dashboard/media/$DANIELA/sbx-comprobante-42.png (customer receipt photo)"
cmd "curl -s $B/api/dashboard/sessions/$DANIELA | jq '.messages[] | select(.image_url) | {ui_type, image_url, event}'"
curl -sS "$B/api/dashboard/sessions/$DANIELA" | jq -c '.messages[] | select(.image_url) | {ui_type, image_url, event}'
cmd "curl -s -o /dev/null -w '%{http_code} %{content_type} %{size_download}' $B/api/dashboard/media/$DANIELA/sbx-comprobante-42.png"
curl -sS -o /dev/null -w 'HTTP %{http_code} %{content_type} %{size_download} bytes\n' "$B/api/dashboard/media/$DANIELA/sbx-comprobante-42.png"

hr "13. Orders — list, detail (Andrés #41), PATCH stage"
cmd "curl -s '$B/api/orders/orders?limit=50' | jq -c '{count, catalog_available} , (.orders[] | {id, display_id, customer, status, pay_status, total_cop, due_iso, overdue, is_draft})'"
curl -sS "$B/api/orders/orders?limit=50" | jq -c '{count, catalog_available}, (.orders[] | {id, display_id, customer, status, pay_status, total_cop, due_iso, overdue, is_draft})'
cmd "curl -s $B/api/orders/orders/$ORDER_41 | jq '{summary: (.summary|{display_id,status,pay_status,total_cop,due_iso,overdue}), items: [.items_detail[]|{title,quantity,unit_price_cop,variant_label}], shipping_address, subtotal_cop, shipping_cop, payment_method_label}'"
curl -sS "$B/api/orders/orders/$ORDER_41" | jq -c '{summary: (.summary|{display_id,status,pay_status,total_cop,due_iso,overdue}), items: [.items_detail[]|{title,quantity,unit_price_cop,variant_label}], shipping_address, subtotal_cop, shipping_cop, payment_method_label}'
cmd "curl -s -X PATCH $B/api/orders/orders/$ORDER_41/stage -d '{\"stage\":\"ready\",\"note\":\"Empacado\"}'"
call PATCH "/api/orders/orders/$ORDER_41/stage" '{"stage":"ready","note":"Empacado"}'; echo "HTTP $CODE"; echo "$BODY" | jq -c .
cmd "curl -s -X PATCH $B/api/orders/orders/$ORDER_41/stage -d '{\"stage\":\"delivered\"}'   # DAG enforced by the real state machine"
call PATCH "/api/orders/orders/$ORDER_41/stage" '{"stage":"delivered"}'; echo "HTTP $CODE"; echo "$BODY" | jq -c .
cmd "curl -s $B/api/orders/orders/$ORDER_41 | jq -c '{status: .summary.status, timeline: [.timeline[] | .label + \" (\" + (.detail // \"\") + \")\"]}'"
curl -sS "$B/api/orders/orders/$ORDER_41" | jq -c '{status: .summary.status, timeline: [.timeline[] | .label + " (" + (.detail // "") + ")"]}'
cmd "tail -1 temporal.log   # the ETA emission the orders API starts (fake Temporal)"
tail -1 temporal.log
cmd "grep '\"method\": \"POST\"' medusa.log | tail -1   # the metadata merge-patch hit the emulated Medusa"
grep '"method": "POST"' medusa.log | tail -1

hr "14. inject.py fire → new GRAVE fire (REST + SSE)"
T0=$(python3 -c 'import time; print(time.time())')
cmd "python3 inject.py fire"; python3 inject.py fire
found=""
for _ in $(seq 1 40); do
  if curl -sS "$B/api/chats/mobile/fires" | jq -e --arg s "$MATEO" '.fires[] | select(.subject.session_id == $s)' >/dev/null; then
    found=yes; break
  fi
  sleep 0.25
done
T1=$(python3 -c 'import time; print(time.time())')
echo "fire visible in /mobile/fires after $(python3 -c "print(round($T1-$T0, 2))") s: ${found:-NO}"
cmd "curl -s $B/api/chats/mobile/fires | jq -c '.fires[] | {fire_id, severity, kind, title, subtitle, getting_worse}'"
curl -sS "$B/api/chats/mobile/fires" | jq -c '.fires[] | {fire_id, severity, kind, title, subtitle, getting_worse}'
sse_seen=""
for _ in $(seq 1 40); do
  if grep -q "\"session_updated\", \"id\": \"$MATEO\"" "$SSE_FILE" 2>/dev/null; then sse_seen=yes; break; fi
  sleep 0.25
done
T2=$(python3 -c 'import time; print(time.time())')
echo "SSE chats.session_updated for $MATEO after $(python3 -c "print(round($T2-$T0, 2))") s: ${sse_seen:-NO}"

hr "14b. inject.py reply → customer message (Valentina writes back: window reopens)"
cmd "python3 inject.py reply $VALENTINA 'Hola! Sí, sigo interesada 😊 ¿me mandas las fotos?'"
python3 inject.py reply "$VALENTINA" 'Hola! Sí, sigo interesada 😊 ¿me mandas las fotos?'
cmd "curl -s $B/api/dashboard/sessions | jq -c '.sessions[] | select(.session_id==\"$VALENTINA\") | {session_id, unanswered_count, last_inbound_ms}'"
curl -sS "$B/api/dashboard/sessions" | jq -c --arg s "$VALENTINA" '.sessions[] | select(.session_id==$s) | {session_id, unanswered_count, last_inbound_ms}'
cmd "curl -s $B/api/chats/mobile/suggestions/$VALENTINA | jq -c '{window_open, suggestions: [.suggestions[].id]}'"
curl -sS "$B/api/chats/mobile/suggestions/$VALENTINA" | jq -c '{window_open, suggestions: [.suggestions[].id]}'

hr "15. SSE stream captured during this run (GET /api/dashboard/events?ticket=…)"
sleep 3  # one more sampler tick for the reply above
cmd "curl -s -X POST $B/api/dashboard/sse-ticket"; curl -sS -X POST "$B/api/dashboard/sse-ticket"; echo
echo "first event line (truncated):"; grep -m1 '^data: ' "$SSE_FILE" | cut -c1-260
echo "all events (domain type id):"
grep '^data: ' "$SSE_FILE" | sed 's/^data: //' | jq -r '"  \(.domain) \(.type) \(.id // "-")"'

hr "16. POST /api/dashboard/sessions/$LAURA/return-to-bot"
call POST "/api/dashboard/sessions/$LAURA/return-to-bot" '{"target_route":"ventas"}'; echo "HTTP $CODE"; echo "$BODY" | jq -c .

hr "17. safety evidence"
if [ -f blocked.log ]; then echo "blocked.log:"; cat blocked.log; else echo "blocked.log absent → the socket guard saw ZERO outbound connection/DNS attempts"; fi
echo "sent.log lines (would-be WhatsApp sends, all FakeSend): $(grep -c 'FakeSend\|Fake Send\|FakeUploadMedia\|typing' sent.log)"
echo "api.log 'FakeSend (no token configured)' lines: $(grep -c 'FakeSend (no token configured)' api.log)"
