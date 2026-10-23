import { request } from './client';

export type FieldKind = 'text' | 'integer' | 'decimal' | 'boolean' | 'date' | 'choice' | 'choices';
export interface InventoryField {
	label: string;
	kind: FieldKind;
	unit?: string | null;
	options?: string[];
	option_labels?: Record<string, string>;
	minimum?: string;
	maximum?: string;
	archived?: boolean;
	help?: string;
}
export interface InventoryCategory {
	label: string;
	parent: string | null;
	fields: string[];
	blocks: string[];
	archived?: boolean;
}
export interface InventoryBlock {
	label: string;
	fields: string[];
	archived?: boolean;
}
export interface InventoryUnit {
	label: string;
	dimension: string;
	factor: string;
	offset?: string;
}
export interface InventorySchema {
	version: number;
	fields: Record<string, InventoryField>;
	categories: Record<string, InventoryCategory>;
	blocks: Record<string, InventoryBlock>;
	units: Record<string, InventoryUnit>;
}
export interface InventoryState {
	revision: number;
	schema: InventorySchema;
}
const root = '/settings/inventory-schema';
export const inventorySchema = {
	load: (signal?: AbortSignal) => request<InventoryState>(root, { signal }),
	preview: (schema: InventorySchema, revision: number) =>
		request<{ templates: Record<string, unknown> }>(`${root}/preview`, {
			method: 'POST',
			body: JSON.stringify({ schema, expected_revision: revision }),
		}),
	apply: (schema: InventorySchema, revision: number) =>
		request<InventoryState>(root, {
			method: 'PUT',
			timeout: 130_000,
			body: JSON.stringify({ schema, expected_revision: revision }),
		}),
	values: (
		revision: number,
		category: string | null,
		values: Record<string, string>,
		input_units: Record<string, string> = {}
	) =>
		request<{ revision: number; values: Record<string, string> }>(`${root}/values`, {
			method: 'POST',
			body: JSON.stringify({ revision, category, values, input_units }),
		}),
};

export function fieldIds(schema: InventorySchema, category: string): string[] {
	const result = new Set<string>();
	const visited = new Set<string>();
	let key: string | null = category;
	while (key && !visited.has(key) && visited.size < 16) {
		visited.add(key);
		const value: InventoryCategory | undefined = schema.categories[key];
		if (!value) break;
		for (const id of value.fields) result.add(id);
		for (const id of value.blocks) {
			if (!schema.blocks[id]?.archived) {
				for (const field of schema.blocks[id]?.fields ?? []) result.add(field);
			}
		}
		key = value.parent;
	}
	return [...result].filter((id) => schema.fields[id] && !schema.fields[id].archived);
}

export function categoryLabel(schema: InventorySchema, id: string): string {
	const labels: string[] = [];
	const seen = new Set<string>();
	let key: string | null = id;
	while (key && !seen.has(key) && seen.size < 16) {
		seen.add(key);
		const category: InventoryCategory | undefined = schema.categories[key];
		if (!category) break;
		labels.unshift(category.label);
		key = category.parent;
	}
	return labels.join(' / ');
}

export function definitionId(): string {
	const bytes = crypto.getRandomValues(new Uint8Array(16));
	return 'user_' + Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
