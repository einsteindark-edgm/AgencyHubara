/**
 * Tipos de la entidad `mba-config` (derivados de los contratos Zod) + metadata
 * de UI: cómo se llama cada campo de `business_info` en la API de Meta y qué
 * significa, para que la vista muestre EXACTAMENTE lo que se enviaría.
 */
import type { z } from "zod";
import type {
  mbaBusinessInfoSchema,
  mbaConfigSchema,
  mbaRequestSchema,
  mbaSkillSchema,
} from "./contracts";

export type MbaConfig = z.infer<typeof mbaConfigSchema>;
export type MbaSkill = z.infer<typeof mbaSkillSchema>;
export type MbaBusinessInfo = z.infer<typeof mbaBusinessInfoSchema>;
export type MbaRequest = z.infer<typeof mbaRequestSchema>;

export type MbaBusinessInfoField = Exclude<keyof MbaBusinessInfo, "contact_info" | "sources">;

export interface BusinessInfoFieldMeta {
  key: MbaBusinessInfoField;
  desc: string;
}

/** Campos de `PUT /agent_config/business_info`, en el orden de la doc de Meta. */
export const BUSINESS_INFO_FIELDS: BusinessInfoFieldMeta[] = [
  { key: "business_description", desc: "Información general del negocio" },
  { key: "payment_method", desc: "Métodos de pago aceptados" },
  { key: "delivery_and_shipping", desc: "Envíos y entregas" },
  { key: "return_policy", desc: "Política de devoluciones y garantía" },
  { key: "purchase_info", desc: "Cómo comprar, descuentos" },
];

export const CONTACT_INFO_FIELDS: { key: "email" | "hours_of_operation" | "address"; desc: string }[] = [
  { key: "hours_of_operation", desc: "Horario de atención" },
  { key: "email", desc: "Correo del negocio" },
  { key: "address", desc: "Dirección física" },
];
