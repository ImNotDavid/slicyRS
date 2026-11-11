import type { ModelCheck, PartPayload, PlateSpec, Transform } from "./api";

export const MAX_PARTS = 12;
export const IDENTITY: Transform = { rotation: [0, 0, 0], scale: 1, position: [0, 0] };

export type ModelInfo = { id: string; name: string; dims: [number, number, number]; checks?: ModelCheck[] };
/** One part on the plate: an uploaded model with its own turn, scale and place. Copies are just more parts. */
export type Part = { uid: string; model: ModelInfo; transform: Transform };

let counter = 0;
export const newPart = (model: ModelInfo, transform: Transform = IDENTITY): Part => ({ uid: `p${Date.now().toString(36)}${counter++}`, model, transform });

export const payload = (p: Part): PartPayload => ({ model_id: p.model.id, ...p.transform });

/** What to send to the server: one part keeps the single-part form, several become a plate. */
export function plateSpec(parts: Part[]): PlateSpec {
  return parts.length === 1 ? payload(parts[0]) : { parts: parts.map(payload) };
}
