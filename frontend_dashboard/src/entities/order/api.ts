/**
 * Hook de orders. El prototipo no tiene endpoint backend — devolvemos el
 * dataset estático envuelto en `useQuery` para que las features tengan la misma
 * forma (`data` / `isLoading` / `isError`) que cuando exista `/api/orders`.
 *
 * Cuando llegue el endpoint real:
 *   1. Mover `MOCK_ORDERS` a un test fixture.
 *   2. Reemplazar `queryFn` por `apiClient.get('/api/orders')` + Zod parse.
 *   3. Las features no requieren cambio (consumen `data` directamente).
 */

import { useQuery } from "@tanstack/react-query";
import { orderKeys } from "./keys";
import type { Order } from "./model";

const MOCK_ORDERS: Order[] = [
  { id:"#1247", customer:"María Camila Restrepo", short:"MR", color:"a", phone:"+57 314 ••• 8821", city:"Bogotá",       channel:"WhatsApp",      status:"delayed",   payStatus:"paid",    payType:"confirmed", items:3, total:124500, due:"hoy",    dueIso:"2026-05-12", dueTime:"09:30", overdue:true, pieces:8, agent:"Sofía",    priority:"alta"   },
  { id:"#1246", customer:"Carlos Andrés Vélez",    short:"CV", color:"b", phone:"+57 301 ••• 4412", city:"Medellín",     channel:"Web",           status:"preparing", payStatus:"paid",    payType:"confirmed", items:2, total:78000,  due:"hoy",    dueIso:"2026-05-12", dueTime:"14:00",                pieces:5, agent:"Sofía",    priority:"alta"   },
  { id:"#1245", customer:"Luisa Fernanda Gómez",   short:"LG", color:"c", phone:"+57 320 ••• 9019", city:"Cali",         channel:"Instagram",     status:"new",       payStatus:"pending", payType:"cod",       items:1, total:36000,  due:"hoy",    dueIso:"2026-05-12", dueTime:"17:30",                pieces:3, agent:"Diego",    priority:"normal" },
  { id:"#1244", customer:"Andrés Felipe Torres",   short:"AT", color:"d", phone:"+57 312 ••• 7723", city:"Bogotá",       channel:"WhatsApp",      status:"ready",     payStatus:"paid",    payType:"confirmed", items:4, total:182000, due:"hoy",    dueIso:"2026-05-12", dueTime:"19:00",                pieces:9, agent:"Sofía",    priority:"normal" },
  { id:"#1243", customer:"Daniela Ortiz Pérez",    short:"DO", color:"e", phone:"+57 318 ••• 2231", city:"Barranquilla", channel:"Web",           status:"preparing", payStatus:"partial", payType:"cod",       items:5, total:215000, due:"mañana", dueIso:"2026-05-13", dueTime:"10:00",                pieces:11,agent:"Diego",    priority:"normal" },
  { id:"#1242", customer:"Mateo Hernández",        short:"MH", color:"f", phone:"+57 305 ••• 5588", city:"Bogotá",       channel:"WhatsApp",      status:"new",       payStatus:"paid",    payType:"confirmed", items:2, total:64000,  due:"mañana", dueIso:"2026-05-13", dueTime:"15:30",                pieces:4, agent:"IA Soporte",priority:"normal" },
  { id:"#1241", customer:"Valentina Cárdenas",     short:"VC", color:"a", phone:"+57 322 ••• 1144", city:"Cartagena",    channel:"Mercado Libre", status:"ready",     payStatus:"paid",    payType:"confirmed", items:1, total:48500,  due:"mañana", dueIso:"2026-05-13", dueTime:"11:00",                pieces:2, agent:"Sofía",    priority:"baja"   },
  { id:"#1240", customer:"Juan Sebastián Mora",    short:"JM", color:"b", phone:"+57 311 ••• 6677", city:"Pereira",      channel:"WhatsApp",      status:"preparing", payStatus:"paid",    payType:"confirmed", items:3, total:96000,  due:"jue 14", dueIso:"2026-05-14", dueTime:"12:00",                pieces:6, agent:"Diego",    priority:"normal" },
  { id:"#1239", customer:"Camila Rojas Suárez",    short:"CR", color:"c", phone:"+57 313 ••• 0099", city:"Bogotá",       channel:"Instagram",     status:"shipping",  payStatus:"paid",    payType:"confirmed", items:2, total:72500,  due:"vie 15", dueIso:"2026-05-15", dueTime:"09:00",                pieces:4, agent:"Sofía",    priority:"normal" },
  { id:"#1238", customer:"Felipe Quintero",        short:"FQ", color:"d", phone:"+57 316 ••• 4477", city:"Manizales",    channel:"Tienda",        status:"shipping",  payStatus:"paid",    payType:"confirmed", items:6, total:268000, due:"vie 15", dueIso:"2026-05-15", dueTime:"14:30",                pieces:14,agent:"Diego",    priority:"alta"   },
  { id:"#1237", customer:"Isabella Martínez",      short:"IM", color:"e", phone:"+57 304 ••• 3322", city:"Bucaramanga",  channel:"Web",           status:"new",       payStatus:"pending", payType:"cod",       items:1, total:42000,  due:"sáb 16", dueIso:"2026-05-16", dueTime:"16:00",                pieces:3, agent:"IA Soporte",priority:"normal" },
  { id:"#1236", customer:"Tomás Echeverri",        short:"TE", color:"f", phone:"+57 318 ••• 5566", city:"Medellín",     channel:"WhatsApp",      status:"preparing", payStatus:"paid",    payType:"confirmed", items:3, total:112000, due:"dom 17", dueIso:"2026-05-17", dueTime:"11:30",                pieces:7, agent:"Sofía",    priority:"normal" },
  { id:"#1235", customer:"Sofía Acosta",           short:"SA", color:"a", phone:"+57 315 ••• 7788", city:"Bogotá",       channel:"Web",           status:"new",       payStatus:"paid",    payType:"confirmed", items:4, total:156000, due:"lun 18", dueIso:"2026-05-18", dueTime:"10:00",                pieces:8, agent:"Diego",    priority:"normal" },
  { id:"#1234", customer:"Nicolás Vergara",        short:"NV", color:"b", phone:"+57 310 ••• 2233", city:"Cali",         channel:"Instagram",     status:"preparing", payStatus:"partial", payType:"cod",       items:2, total:84000,  due:"mar 19", dueIso:"2026-05-19", dueTime:"13:00",                pieces:5, agent:"Sofía",    priority:"baja"   },
  { id:"#1233", customer:"Laura Moncada",          short:"LM", color:"c", phone:"+57 320 ••• 9911", city:"Bogotá",       channel:"WhatsApp",      status:"delivered", payStatus:"paid",    payType:"confirmed", items:2, total:74500,  due:"ayer",   dueIso:"2026-05-11", dueTime:"15:00",                pieces:4, agent:"Sofía",    priority:"normal" },
  { id:"#1232", customer:"Esteban Patiño",         short:"EP", color:"d", phone:"+57 322 ••• 8855", city:"Medellín",     channel:"Web",           status:"delivered", payStatus:"paid",    payType:"confirmed", items:5, total:198000, due:"ayer",   dueIso:"2026-05-11", dueTime:"11:00",                pieces:12,agent:"Diego",    priority:"normal" },
  { id:"#1231", customer:"Sara Botero",            short:"SB", color:"e", phone:"+57 313 ••• 2244", city:"Bogotá",       channel:"Instagram",     status:"cancelled", payStatus:"refund",  payType:"confirmed", items:1, total:36000,  due:"ayer",   dueIso:"2026-05-11", dueTime:"17:00",                pieces:0, agent:"Sofía",    priority:"normal" },
];

export function useOrders() {
  return useQuery({
    queryKey: orderKeys.list(),
    queryFn: async () => MOCK_ORDERS,
    staleTime: Infinity,
  });
}
