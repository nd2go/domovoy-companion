<script lang="ts">
	import { untrack } from 'svelte';
	import { getInitPromise } from '$lib/services/bootstrap';
	import { collectionStore } from '$lib/stores/collection.svelte';
	import { authStore } from '$lib/stores/auth.svelte';
	import type { ReviewItem } from '$lib/types';
	import {
		inventorySchema,
		categoryLabel,
		definitionId,
		fieldIds,
		type InventoryState,
		type InventorySchema,
		type FieldKind,
	} from '$lib/api/inventorySchema';

	let {
		item = $bindable(),
		available = $bindable(),
		disabled = false,
	}: {
		item: ReviewItem;
		available: boolean;
		disabled?: boolean;
	} = $props();
	let registry = $state<InventoryState | null>(null);
	let error = $state('');
	let busy = $state(false);
	let inputUnits = $state<Record<string, string>>({});
	let extraField = $state('');
	let newLabel = $state('');
	let newKind = $state<FieldKind>('text');
	let newUnit = $state('');
	let newChoices = $state('');
	let includeTemplate = $state(false);
	let proposal = $state<{ id: string; schema: InventorySchema } | null>(null);
	let generation = 0;
	let operation = 0;
	const category = $derived(item.category_id ?? item.custom_fields?.inventory_category ?? '');
	const displayedFields = $derived(
		registry
			? [
					...new Set([
						...(category && registry.schema.categories[category]
							? fieldIds(registry.schema, category)
							: []),
						...Object.keys(item.custom_fields ?? {}),
					]),
				].filter((id) => !['inventory_category', 'quantity_unit'].includes(id))
			: []
	);

	$effect(() => {
		const group = collectionStore.selectedId;
		const identity = authStore.contextId;
		untrack(() => {
			const current = ++generation;
			operation++;
			busy = false;
			proposal = null;
			available = false;
			registry = null;
			void load(current);
		});
		void group;
		void identity;
	});
	$effect(() => {
		const currentItem = item;
		untrack(() => {
			operation++;
			busy = false;
			inputUnits = {};
			proposal = null;
			error = '';
		});
		void currentItem;
	});

	async function load(current: number) {
		error = '';
		try {
			await getInitPromise();
			const loaded = await inventorySchema.load();
			if (current !== generation) return;
			if (!loaded.schema) throw new Error('Inventory definitions are unavailable.');
			registry = loaded;
			available = true;
		} catch (e) {
			if (current === generation)
				error = e instanceof Error ? e.message : 'Cannot load properties.';
		}
	}

	async function reloadDefinitions() {
		const current = ++generation;
		await load(current);
		if (current === generation && registry && item.schema_revision !== undefined) {
			item.schema_revision = registry.revision;
		}
	}

	function updateValue(id: string, value: string) {
		item.custom_fields = { ...item.custom_fields, [id]: value };
		if (registry) item.schema_revision ??= registry.revision;
		error = '';
	}

	function selectCategory(value: string) {
		if (!registry) return;
		item.category_id = value || null;
		item.schema_revision = registry.revision;
		item.custom_fields = { ...item.custom_fields, inventory_category: value, quantity_unit: 'pcs' };
		proposal = null;
		error = '';
	}

	function choices(raw: string): string[] {
		try {
			const value: unknown = JSON.parse(raw);
			return Array.isArray(value) && value.every((x) => typeof x === 'string') ? value : [];
		} catch {
			return [];
		}
	}

	export async function validate(): Promise<boolean> {
		if (!registry) {
			if (item.schema_revision === undefined) return true;
			error = 'Reload inventory definitions before saving this card.';
			return false;
		}
		const current = ++operation;
		const target = item;
		busy = true;
		error = '';
		try {
			const result = await inventorySchema.values(
				item.schema_revision ?? registry.revision,
				category || null,
				item.custom_fields ?? {},
				inputUnits
			);
			if (current !== operation || item !== target) return false;
			item.custom_fields = result.values;
			item.schema_revision = result.revision;
			item.category_id = category || null;
			inputUnits = {};
			return true;
		} catch (e) {
			if (current === operation && item === target)
				error = e instanceof Error ? e.message : 'Check the property values.';
			return false;
		} finally {
			if (current === operation) busy = false;
		}
	}

	async function previewProperty() {
		if (!registry || !newLabel.trim()) return;
		const current = ++operation;
		const target = item;
		busy = true;
		error = '';
		try {
			const schema = structuredClone($state.snapshot(registry.schema));
			const id = definitionId();
			schema.fields[id] = { label: newLabel.trim(), kind: newKind };
			if (['integer', 'decimal'].includes(newKind) && newUnit) schema.fields[id].unit = newUnit;
			if (['choice', 'choices'].includes(newKind)) {
				const labels = newChoices
					.split(',')
					.map((x) => x.trim())
					.filter(Boolean);
				const entries = labels.map((label) => [definitionId(), label]);
				schema.fields[id].options = entries.map(([key]) => key);
				schema.fields[id].option_labels = Object.fromEntries(entries);
			}
			if (includeTemplate && category) schema.categories[category].fields.push(id);
			await inventorySchema.preview(schema, registry.revision);
			if (current !== operation || item !== target) return;
			proposal = { id, schema };
		} catch (e) {
			if (current === operation && item === target)
				error = e instanceof Error ? e.message : 'Cannot check the new property.';
		} finally {
			if (current === operation) busy = false;
		}
	}

	async function acceptProperty() {
		if (!proposal || !registry) return;
		const current = ++operation;
		const target = item;
		busy = true;
		error = '';
		try {
			const result = await inventorySchema.apply(
				$state.snapshot(proposal.schema),
				registry.revision
			);
			if (current !== operation || item !== target) return;
			registry = result;
			item.schema_revision = registry.revision;
			updateValue(proposal.id, '');
			proposal = null;
			newLabel = '';
		} catch (e) {
			if (current === operation && item === target)
				error = e instanceof Error ? e.message : 'Cannot add the property.';
		} finally {
			if (current === operation) busy = false;
		}
	}
</script>

<section class="space-y-3 border-t border-neutral-700 pt-4" aria-label="Inventory properties">
	<h3 class="font-semibold">Category and properties</h3>
	{#if error}<p role="alert" class="text-error-400">{error} Your values are kept.</p>
		<button
			type="button"
			class="rounded border border-neutral-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
			onclick={reloadDefinitions}
			disabled={busy || disabled}>Reload definitions and review</button
		>{/if}
	{#if available && registry}
		<fieldset class="space-y-3" disabled={disabled || busy}>
			<label class="block"
				>Item category<select
					class="input mt-1 w-full"
					value={category}
					onchange={(event) => selectCategory(event.currentTarget.value)}
				>
					<option value="">Unknown category</option>
					{#if category && (!registry.schema.categories[category] || registry.schema.categories[category].archived)}<option
							value={category}
							>{registry.schema.categories[category]?.label ?? category} (saved)</option
						>{/if}
					{#each Object.entries(registry.schema.categories).filter(([, value]) => !value.archived) as [id] (id)}<option
							value={id}>{categoryLabel(registry.schema, id)}</option
						>{/each}
				</select></label
			>
			{#each displayedFields as id (id)}
				{@const field = registry.schema.fields[id]}
				{@const value = item.custom_fields?.[id] ?? ''}
				<div>
					<label for={'property-' + id} class="label"
						>{field?.label ?? id}{field?.archived ? ' (archived)' : ''}</label
					>
					{#if field?.kind === 'boolean'}
						<select
							id={'property-' + id}
							class="input w-full"
							{value}
							onchange={(event) => updateValue(id, event.currentTarget.value)}
							><option value="">Unknown</option><option value="true">Yes</option><option
								value="false">No</option
							></select
						>
					{:else if field?.kind === 'choice' || field?.kind === 'choices'}
						<select
							id={'property-' + id}
							class="input w-full"
							multiple={field.kind === 'choices'}
							value={field.kind === 'choices' ? choices(value) : value}
							onchange={(event) =>
								updateValue(
									id,
									field.kind === 'choices'
										? JSON.stringify([...event.currentTarget.selectedOptions].map((x) => x.value))
										: event.currentTarget.value
								)}
						>
							{#if field.kind === 'choice'}<option value="">Unknown</option>{/if}
							{#each field.options ?? [] as choice (choice)}<option value={choice}
									>{field.option_labels?.[choice] ?? choice}</option
								>{/each}
						</select>
					{:else}
						<input
							id={'property-' + id}
							class="input w-full"
							type={field?.kind === 'date' ? 'date' : 'text'}
							inputmode={field?.kind === 'decimal' || field?.kind === 'integer'
								? 'decimal'
								: 'text'}
							maxlength="500"
							{value}
							oninput={(event) => updateValue(id, event.currentTarget.value)}
						/>
					{/if}
					{#if field?.kind === 'choices'}
						<button type="button" class="mt-1 text-sm underline" onclick={() => updateValue(id, '')}
							>Mark {field.label} as unknown</button
						>
					{/if}
					{#if field?.unit}
						<label class="mt-1 block text-sm"
							>Input unit for {field.label}<select
								class="input-sm ml-2"
								value={inputUnits[id] ?? field.unit}
								onchange={(event) => {
									inputUnits = { ...inputUnits, [id]: event.currentTarget.value };
									item.schema_revision ??= registry?.revision;
								}}
							>
								{#each Object.entries(registry.schema.units).filter(([, unit]) => unit.dimension === registry?.schema.units[field.unit!]?.dimension) as [key, unit] (key)}<option
										value={key}>{unit.label}</option
									>{/each}
							</select></label
						>
					{/if}
				</div>
			{/each}
			<div class="space-y-2">
				<label class="block"
					>Additional property<select class="input mt-1 w-full" bind:value={extraField}
						><option value="">Choose a property</option
						>{#each Object.entries(registry.schema.fields).filter(([id, field]) => !displayedFields.includes(id) && !field.archived && !['inventory_category', 'quantity_unit'].includes(id)) as [id, field] (id)}<option
								value={id}>{field.label}</option
							>{/each}</select
					></label
				>
				<button
					type="button"
					class="rounded border border-neutral-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
					disabled={!extraField}
					onclick={() => {
						updateValue(extraField, '');
						extraField = '';
					}}>Add to this item</button
				>
			</div>
			<details class="space-y-2">
				<summary class="cursor-pointer">Create a new property</summary>
				<fieldset disabled={!!proposal} class="space-y-2">
					<label class="block"
						>Property name<input
							class="input mt-1 w-full"
							bind:value={newLabel}
							maxlength="255"
						/></label
					>
					<label class="block"
						>Property value type<select class="input mt-1 w-full" bind:value={newKind}
							>{#each ['text', 'integer', 'decimal', 'boolean', 'date', 'choice', 'choices'] as kind (kind)}<option
									>{kind}</option
								>{/each}</select
						></label
					>
					{#if ['integer', 'decimal'].includes(newKind)}<label class="block"
							>Property unit<select class="input mt-1 w-full" bind:value={newUnit}
								><option value="">No unit</option
								>{#each Object.entries(registry.schema.units) as [id, unit] (id)}<option value={id}
										>{unit.label}</option
									>{/each}</select
							></label
						>{/if}
					{#if ['choice', 'choices'].includes(newKind)}<label class="block"
							>Choice labels, separated by commas<input
								class="input mt-1 w-full"
								bind:value={newChoices}
								maxlength="500"
							/></label
						>{/if}
					<label class="flex gap-2"
						><input
							type="checkbox"
							bind:checked={includeTemplate}
							disabled={!category || !registry.schema.categories[category]}
						/>Also add to the category template</label
					>
				</fieldset>
				{#if proposal}
					<p>
						{includeTemplate
							? 'This property will be offered for future items in this category.'
							: 'This property will be added only to this item.'}
					</p>
					<button
						type="button"
						class="rounded bg-primary-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
						onclick={acceptProperty}>Confirm new property</button
					>
					<button
						type="button"
						class="rounded border border-neutral-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
						onclick={() => {
							proposal = null;
						}}>Cancel property</button
					>
				{:else}<button
						type="button"
						class="rounded border border-neutral-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
						onclick={previewProperty}
						disabled={!newLabel.trim()}>Preview new property</button
					>{/if}
			</details>
		</fieldset>
	{/if}
</section>
