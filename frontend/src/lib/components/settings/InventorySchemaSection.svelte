<script lang="ts">
	import { untrack } from 'svelte';
	import { getInitPromise } from '$lib/services/bootstrap';
	import { collectionStore } from '$lib/stores/collection.svelte';
	import { authStore } from '$lib/stores/auth.svelte';
	import {
		inventorySchema,
		categoryLabel,
		definitionId,
		type InventorySchema,
		type InventoryState,
		type FieldKind,
	} from '$lib/api/inventorySchema';

	let original = $state<InventoryState | null>(null);
	let draft = $state<InventorySchema | null>(null);
	let error = $state('');
	let message = $state('');
	let busy = $state(false);
	let previewed = $state(false);
	let tab = $state<'categories' | 'blocks' | 'fields' | 'units'>('categories');
	let selected = $state('');
	let newLabel = $state('');
	let newChoice = $state('');
	let previewCount = $state(0);
	let changeSummary = $state<string[]>([]);
	let generation = 0;
	const kinds: FieldKind[] = ['text', 'integer', 'decimal', 'boolean', 'date', 'choice', 'choices'];
	const entries = $derived(draft ? Object.entries(draft[tab]) : []);
	const category = $derived(tab === 'categories' ? draft?.categories[selected] : undefined);
	const block = $derived(tab === 'blocks' ? draft?.blocks[selected] : undefined);
	const field = $derived(tab === 'fields' ? draft?.fields[selected] : undefined);
	const unit = $derived(tab === 'units' ? draft?.units[selected] : undefined);

	$effect(() => {
		const group = collectionStore.selectedId;
		const identity = authStore.contextId;
		untrack(() => {
			const current = ++generation;
			original = null;
			draft = null;
			previewed = false;
			void load(current);
		});
		void group;
		void identity;
	});

	async function load(current = ++generation) {
		busy = true;
		error = '';
		try {
			await getInitPromise();
			const value = await inventorySchema.load();
			if (current !== generation) return;
			if (!value.schema) throw new Error('Inventory definitions are unavailable.');
			original = value;
			draft = structuredClone(value.schema);
			selected = Object.keys(value.schema[tab])[0] ?? '';
		} catch (e) {
			if (current === generation)
				error = e instanceof Error ? e.message : 'Cannot load definitions.';
		} finally {
			if (current === generation) busy = false;
		}
	}

	function changed() {
		previewed = false;
		message = '';
	}

	function addDefinition() {
		if (!draft || !newLabel.trim()) return;
		const id = definitionId();
		const label = newLabel.trim();
		if (tab === 'categories')
			draft.categories[id] = { label, parent: null, fields: [], blocks: [] };
		if (tab === 'blocks') draft.blocks[id] = { label, fields: [] };
		if (tab === 'fields') draft.fields[id] = { label, kind: 'text' };
		if (tab === 'units') draft.units[id] = { label, dimension: 'length', factor: '1', offset: '0' };
		selected = id;
		newLabel = '';
		changed();
	}

	async function preview() {
		if (!draft || !original) return;
		const current = generation;
		busy = true;
		error = '';
		try {
			const result = await inventorySchema.preview($state.snapshot(draft), original.revision);
			if (current !== generation) return;
			changeSummary = [];
			for (const group of ['categories', 'blocks', 'fields', 'units'] as const) {
				for (const [id, value] of Object.entries(draft[group])) {
					const before = original.schema[group][id];
					if (JSON.stringify(before) !== JSON.stringify(value)) {
						changeSummary.push(`${before ? 'Update' : 'Add'} ${group}: ${value.label}`);
					}
				}
			}
			previewCount = Object.keys(result.templates).length;
			previewed = true;
		} catch (e) {
			if (current === generation)
				error = e instanceof Error ? e.message : 'Cannot check definitions.';
		} finally {
			if (current === generation) busy = false;
		}
	}

	async function apply() {
		if (!draft || !original || !previewed) return;
		busy = true;
		error = '';
		const current = generation;
		try {
			const result = await inventorySchema.apply($state.snapshot(draft), original.revision);
			if (current !== generation) return;
			original = result;
			draft = structuredClone(result.schema);
			previewed = false;
			message = 'Definitions saved. Existing inventory was kept.';
		} catch (e) {
			if (current === generation)
				error = e instanceof Error ? e.message : 'Cannot apply definitions.';
		} finally {
			if (current === generation) busy = false;
		}
	}

	async function importFile(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file || !original) return;
		const current = generation;
		const revision = original.revision;
		busy = true;
		error = '';
		try {
			if (file.size > 2 * 1024 * 1024) throw new Error('The import limit is 2 MiB.');
			const parsed = JSON.parse(await file.text());
			if (current !== generation) return;
			const schema: InventorySchema = parsed.schema ?? parsed;
			await inventorySchema.preview(schema, revision);
			if (current !== generation) return;
			draft = schema;
			selected = Object.keys(schema[tab])[0] ?? '';
			changed();
		} catch (e) {
			if (current === generation)
				error = e instanceof Error ? e.message : 'Invalid definition file.';
		} finally {
			input.value = '';
			if (current === generation) busy = false;
		}
	}

	function exportFile() {
		if (!draft) return;
		const url = URL.createObjectURL(
			new Blob([JSON.stringify($state.snapshot(draft), null, 2)], { type: 'application/json' })
		);
		const link = document.createElement('a');
		link.href = url;
		link.download = 'inventory-schema.json';
		link.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
</script>

<section class="card space-y-4" aria-labelledby="inventory-heading">
	<h2 id="inventory-heading" class="text-xl font-semibold">Categories and properties</h2>
	<p class="text-sm text-neutral-400">
		Create optional templates for any kind of item. Shared blocks reuse the same properties.
	</p>
	{#if error}<p role="alert" class="text-error-400">{error} Your edits are kept.</p>{/if}
	{#if message}<p role="status">{message}</p>{/if}
	{#if draft}
		<fieldset disabled={busy} class="space-y-4" oninput={changed} onchange={changed}>
			<div class="flex flex-wrap gap-2">
				{#each ['categories', 'blocks', 'fields', 'units'] as name (name)}
					<button
						type="button"
						class="rounded border border-neutral-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
						aria-pressed={tab === name}
						onclick={() => {
							tab = name as typeof tab;
							selected = Object.keys(draft![tab])[0] ?? '';
						}}>{name === 'fields' ? 'Properties' : name[0].toUpperCase() + name.slice(1)}</button
					>
				{/each}
			</div>
			<label class="block"
				>Edit definition
				<select class="input mt-1 w-full" bind:value={selected}>
					{#each entries as [id, value] (id)}<option value={id}
							>{value.label}{'archived' in value && value.archived ? ' (archived)' : ''}</option
						>{/each}
				</select>
			</label>
			<div class="flex flex-wrap gap-2">
				<label class="min-w-0 flex-1"
					>New definition name<input
						class="input mt-1 w-full"
						maxlength="255"
						bind:value={newLabel}
					/></label
				>
				<button
					type="button"
					class="self-end rounded border border-neutral-600 px-3 py-2"
					onclick={addDefinition}
					disabled={!newLabel.trim()}>Add definition</button
				>
			</div>
			{#if category || block || field || unit}
				<label class="block"
					>Display name<input
						class="input mt-1 w-full"
						maxlength="255"
						bind:value={draft[tab][selected].label}
					/></label
				>
			{/if}
			{#if category}
				<label class="block"
					>Parent category<select class="input mt-1 w-full" bind:value={category.parent}>
						<option value={null}>No parent</option>
						{#each Object.entries(draft.categories).filter(([id, value]) => id !== selected && !value.archived) as [id] (id)}<option
								value={id}>{categoryLabel(draft, id)}</option
							>{/each}
					</select></label
				>
				<label class="block"
					>Shared blocks<select
						aria-label="Shared blocks"
						class="input mt-1 w-full"
						multiple
						bind:value={category.blocks}
					>
						{#each Object.entries(draft.blocks).filter(([, value]) => !value.archived) as [id, value] (id)}<option
								value={id}>{value.label}</option
							>{/each}
					</select></label
				>
			{/if}
			{#if category || block}
				<label class="block"
					>Template properties<select
						aria-label="Template properties"
						class="input mt-1 w-full"
						multiple
						bind:value={
							() => (category ?? block)?.fields ?? [],
							(value) => {
								const target = category ?? block;
								if (target) target.fields = value;
							}
						}
					>
						{#each Object.entries(draft.fields).filter(([, value]) => !value.archived) as [id, value] (id)}<option
								value={id}>{value.label}</option
							>{/each}
					</select></label
				>
			{/if}
			{#if field}
				<label class="block"
					>Value type<select
						class="input mt-1 w-full"
						value={field.kind}
						onchange={(event) => {
							if (!field) return;
							field.kind = event.currentTarget.value as FieldKind;
							if (!['integer', 'decimal'].includes(field.kind)) delete field.unit;
							if (!['choice', 'choices'].includes(field.kind)) {
								delete field.options;
								delete field.option_labels;
							}
						}}
						disabled={!!original?.schema.fields[selected]}
						>{#each kinds as kind (kind)}<option>{kind}</option>{/each}</select
					></label
				>
				{#if field.kind === 'decimal' || field.kind === 'integer'}
					<label class="block"
						>Unit<select
							class="input mt-1 w-full"
							bind:value={field.unit}
							disabled={!!original?.schema.fields[selected]}
							><option value={undefined}>No unit</option
							>{#each Object.entries(draft.units) as [id, value] (id)}<option value={id}
									>{value.label}</option
								>{/each}</select
						></label
					>
				{/if}
				{#if field.kind === 'choice' || field.kind === 'choices'}
					{#each field.options ?? [] as id (id)}
						<label class="block"
							>Choice label<input
								class="input mt-1 w-full"
								value={field.option_labels?.[id] ?? id}
								maxlength="255"
								oninput={(event) => {
									if (field)
										field.option_labels = {
											...field.option_labels,
											[id]: event.currentTarget.value,
										};
								}}
							/></label
						>
					{/each}
					<label class="block"
						>New choice<input
							class="input mt-1 w-full"
							bind:value={newChoice}
							maxlength="255"
						/></label
					>
					<button
						type="button"
						class="rounded border border-neutral-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
						disabled={!newChoice.trim()}
						onclick={() => {
							if (!field) return;
							const id = definitionId();
							field.options = [...(field.options ?? []), id];
							field.option_labels = { ...field.option_labels, [id]: newChoice.trim() };
							newChoice = '';
							changed();
						}}>Add choice</button
					>
				{/if}
			{/if}
			{#if unit}
				{#each ['dimension', 'factor', 'offset'] as key (key)}
					<label class="block"
						>{key}<input
							class="input mt-1 w-full"
							maxlength="64"
							bind:value={unit[key as keyof typeof unit]}
							disabled={!!original?.schema.units[selected]}
						/></label
					>
				{/each}
			{/if}
			{#if category || block || field}
				<label class="flex gap-2"
					><input
						type="checkbox"
						bind:checked={
							() => (category ?? block ?? field)?.archived ?? false,
							(value) => {
								const target = category ?? block ?? field;
								if (target) target.archived = value;
							}
						}
					/>Archived</label
				>
			{/if}
		</fieldset>
		<div class="flex flex-wrap gap-2">
			<button
				type="button"
				class="rounded border border-neutral-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
				onclick={preview}
				disabled={busy}>Preview changes</button
			>
			<button
				type="button"
				class="rounded bg-primary-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
				onclick={apply}
				disabled={busy || !previewed}>Apply definitions</button
			>
			<button
				type="button"
				class="rounded border border-neutral-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
				onclick={exportFile}
				disabled={busy}>Export definitions</button
			>
			<label class="cursor-pointer rounded border border-neutral-600 px-3 py-2"
				>Import definitions<input
					type="file"
					accept="application/json,.json"
					class="sr-only"
					onchange={importFile}
					disabled={busy}
				/></label
			>
			<button
				type="button"
				class="rounded border border-neutral-600 px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50"
				onclick={() => void load()}
				disabled={busy}>Discard edits and reload</button
			>
		</div>
		{#if previewed}<ul aria-label="Definition changes" class="list-inside list-disc text-sm">
				{#each changeSummary.slice(0, 40) as change, index (index)}<li>{change}</li>{/each}
			</ul>
			<p role="status">
				Checked {previewCount} templates and {changeSummary.length} definition changes. Applying changes
				keeps existing inventory values.
			</p>{/if}
	{:else if busy}<p role="status">Loading inventory definitions…</p>
	{:else}<button type="button" onclick={() => void load()}>Retry inventory definitions</button>{/if}
</section>
