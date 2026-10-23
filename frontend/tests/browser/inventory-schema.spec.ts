import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import type { InventorySchema } from '../../src/lib/api/inventorySchema';

const seed: InventorySchema = JSON.parse(
	readFileSync('../src/homebox_companion/inventory_schema_seed.json', 'utf8')
);
const json = (body: unknown, status = 200) => ({
	status,
	contentType: 'application/json',
	body: JSON.stringify(body),
});

async function installApi(page: Page) {
	const model = {
		schema: structuredClone(seed),
		revision: 0,
		conflict: false,
		submitted: [] as Record<string, unknown>[],
		detectedFields: { diameter_mm: '2.5', owner_note: 'Keep me' },
	};
	await page.route('**/api/**', async (route) => {
		const req = route.request();
		const path = new URL(req.url()).pathname;
		if (path === '/api/config')
			return route.fulfill(
				json({
					auth_mode: 'api_key',
					is_demo_mode: false,
					demo_mode_explicit: false,
					homebox_url: 'http://homebox.test',
					llm_model: 'test',
					update_check_enabled: false,
					image_quality: 'high',
					log_level: 'INFO',
					capture_max_images: 10,
					capture_max_file_size_mb: 20,
					print_enabled: false,
				})
			);
		if (path === '/api/homebox/connection')
			return route.fulfill(
				json({
					connected: true,
					context_id: 'deployment:user',
					user_id: 'user-1',
					default_group_id: 'g1',
				})
			);
		if (path === '/api/groups') return route.fulfill(json([{ id: 'g1', name: 'Personal' }]));
		if (path === '/api/version')
			return route.fulfill(
				json({ version: 'test', latest_version: null, update_available: false })
			);
		if (path === '/api/tags' || path === '/api/settings/custom-fields')
			return route.fulfill(json([]));
		if (path === '/api/locations/tree')
			return route.fulfill(json([{ id: 'loc-1', name: 'Storage', itemCount: 0, children: [] }]));
		if (path === '/api/locations/loc-1')
			return route.fulfill(json({ id: 'loc-1', name: 'Storage', itemCount: 0, children: [] }));
		if (path === '/api/settings/inventory-schema') {
			if (req.method() === 'PUT') {
				const body = req.postDataJSON();
				if (model.conflict || body.expected_revision !== model.revision)
					return route.fulfill(json({ detail: 'Schema revision changed' }, 409));
				model.schema = body.schema;
				model.revision++;
			}
			return route.fulfill(json({ schema: model.schema, revision: model.revision }));
		}
		if (path === '/api/settings/inventory-schema/preview')
			return route.fulfill(json({ templates: model.schema.categories }));
		if (path === '/api/settings/inventory-schema/values') {
			const body = req.postDataJSON();
			if (body.revision !== model.revision)
				return route.fulfill(json({ detail: 'Schema revision changed' }, 409));
			const values = { ...body.values };
			for (const [id, value] of Object.entries(values)) {
				if (
					['integer', 'decimal'].includes(model.schema.fields[id]?.kind) &&
					value !== '' &&
					!Number.isFinite(Number(String(value).replace(',', '.')))
				) {
					return route.fulfill(json({ detail: 'Invalid numeric property' }, 422));
				}
			}
			for (const [id, unit] of Object.entries(body.input_units ?? {})) {
				const target = model.schema.fields[id].unit!;
				values[id] = String(
					(Number(values[id].replace(',', '.')) *
						Number(model.schema.units[unit as string].factor)) /
						Number(model.schema.units[target].factor)
				);
			}
			if (body.category) values.inventory_category = body.category;
			return route.fulfill(json({ revision: model.revision, values }));
		}
		if (path === '/api/tools/vision/detect')
			return route.fulfill(
				json({
					items: [
						{
							name: 'Control item',
							quantity: 1,
							custom_fields: model.detectedFields,
						},
					],
					compressed_images: [],
				})
			);
		if (path === '/api/items' && req.method() === 'POST') {
			model.submitted.push(req.postDataJSON().items[0]);
			return route.fulfill(json({ created: [{ id: 'saved-control' }], errors: [] }));
		}
		if (path.endsWith('/attachments')) return route.fulfill(json({}));
		return route.fulfill(json({}));
	});
	return model;
}

async function openReview(page: Page) {
	await page.goto('/location');
	await page.getByRole('button', { name: /Storage/ }).click();
	await page.getByRole('button', { name: 'Select current location' }).click();
	await page.getByRole('button', { name: 'Continue to Capture' }).click();
	await page
		.locator('input[type=file]')
		.first()
		.setInputFiles({
			name: 'item.png',
			mimeType: 'image/png',
			buffer: Buffer.from(
				'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
				'base64'
			),
		});
	await page.getByRole('button', { name: /Analyze with AI/ }).click();
	await expect(page).toHaveURL(/\/review$/);
	await expect(page.getByLabel('Item category')).toBeVisible();
}

test('editor creates a property, shared block and new category without code changes', async ({
	page,
}) => {
	const model = await installApi(page);
	await page.goto('/settings');
	const editor = page.getByRole('region', { name: 'Categories and properties' });
	await editor.getByRole('button', { name: 'Properties', exact: true }).click();
	await editor.getByLabel('New definition name').fill('Batch length');
	await editor.getByRole('button', { name: 'Add definition' }).click();
	await editor.getByLabel('Value type').selectOption('decimal');
	await editor.getByRole('combobox', { name: 'Unit', exact: true }).selectOption('mm');
	await editor.getByRole('button', { name: 'Preview changes' }).click();
	await editor.getByRole('button', { name: 'Apply definitions' }).click();
	await expect(editor.getByRole('status')).toContainText('Definitions saved');
	const fieldId = Object.keys(model.schema.fields).find(
		(id) => model.schema.fields[id].label === 'Batch length'
	)!;
	await editor.getByRole('button', { name: 'Blocks', exact: true }).click();
	await editor.getByLabel('New definition name').fill('Batch properties');
	await editor.getByRole('button', { name: 'Add definition' }).click();
	await editor
		.getByRole('listbox', { name: 'Template properties', exact: true })
		.selectOption([fieldId]);
	await editor.getByRole('button', { name: 'Preview changes' }).click();
	await editor.getByRole('button', { name: 'Apply definitions' }).click();
	await expect(editor.getByRole('status')).toContainText('Definitions saved');
	const blockId = Object.keys(model.schema.blocks).find(
		(id) => model.schema.blocks[id].label === 'Batch properties'
	)!;
	await editor.getByRole('button', { name: 'Categories', exact: true }).click();
	await editor.getByLabel('New definition name').fill('Marine supplies');
	await editor.getByRole('button', { name: 'Add definition' }).click();
	await editor.getByRole('listbox', { name: 'Shared blocks', exact: true }).selectOption([blockId]);
	await editor.getByRole('button', { name: 'Preview changes' }).click();
	await editor.getByRole('button', { name: 'Apply definitions' }).click();
	await expect(editor.getByRole('status')).toContainText('Definitions saved');
	expect(
		Object.values(model.schema.categories).find((x) => x.label === 'Marine supplies')?.blocks
	).toContain(blockId);
	expect(model.schema.blocks[blockId].fields).toEqual([fieldId]);
});

test('revision conflict keeps editor changes', async ({ page }) => {
	const model = await installApi(page);
	await page.goto('/settings');
	const editor = page.getByRole('region', { name: 'Categories and properties' });
	await editor.getByLabel('Display name').fill('Changed label');
	await editor.getByRole('button', { name: 'Preview changes' }).click();
	model.conflict = true;
	await editor.getByRole('button', { name: 'Apply definitions' }).click();
	await expect(editor.getByRole('alert')).toContainText('Schema revision changed');
	await expect(editor.getByLabel('Display name')).toHaveValue('Changed label');
	expect(model.revision).toBe(0);
});

test('category changes retain properties and unit conversion reaches submission', async ({
	page,
}) => {
	const model = await installApi(page);
	await openReview(page);
	await page.getByLabel('Item category').selectOption('screw');
	await page.getByLabel('Diameter', { exact: true }).fill('0.6');
	await page.getByLabel('Input unit for Diameter').selectOption('cm');
	await page.getByLabel('Item category').selectOption('clothing');
	await expect(page.getByLabel('Diameter', { exact: true })).toHaveValue('0.6');
	await expect(page.getByLabel('Input unit for Diameter')).toHaveValue('cm');
	await page.getByLabel('Item category').selectOption('screw');
	await page.getByRole('button', { name: 'Confirm', exact: true }).click();
	await expect(page).toHaveURL(/\/summary$/);
	await page.getByRole('button', { name: /Submit All Items/ }).click();
	await expect.poll(() => model.submitted.length).toBe(1);
	expect(model.submitted[0]).toMatchObject({
		schema_revision: 0,
		category_id: 'screw',
		custom_fields: {
			diameter_mm: '6',
			owner_note: 'Keep me',
			inventory_category: 'screw',
			quantity_unit: 'pcs',
		},
	});
});

test('new local property leaves its category template unchanged', async ({ page }) => {
	const model = await installApi(page);
	await openReview(page);
	await page.getByLabel('Item category').selectOption('screw');
	await page.getByText('Create a new property', { exact: true }).click();
	await page.getByLabel('Property name', { exact: true }).fill('Store code');
	await page.getByRole('button', { name: 'Preview new property' }).click();
	expect(model.revision).toBe(0);
	await page.getByRole('button', { name: 'Confirm new property' }).click();
	await expect(page.getByLabel('Store code', { exact: true })).toBeVisible();
	const id = Object.keys(model.schema.fields).find(
		(id) => model.schema.fields[id].label === 'Store code'
	)!;
	expect(model.schema.categories.screw.fields).not.toContain(id);
	await page.getByLabel('Store code', { exact: true }).fill('AX9');
	await page.getByRole('button', { name: 'Confirm', exact: true }).click();
	await page.getByRole('button', { name: /Submit All Items/ }).click();
	await expect.poll(() => model.submitted.length).toBe(1);
	expect((model.submitted[0].custom_fields as Record<string, string>)[id]).toBe('AX9');
});

for (const width of [393, 412]) {
	test(`editor fits a mobile viewport ${width}`, async ({ page }) => {
		await page.setViewportSize({ width, height: 852 });
		await installApi(page);
		await page.goto('/settings');
		const editor = page.getByRole('region', { name: 'Categories and properties' });
		await expect(editor.getByLabel('Edit definition')).toBeVisible();
		expect(
			await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
		).toBe(true);
	});
}

test('confirmed properties and schema revision survive session recovery', async ({ page }) => {
	const model = await installApi(page);
	await openReview(page);
	await page.getByLabel('Item category').selectOption('screw');
	await page.getByLabel('Diameter', { exact: true }).fill('6.2');
	await page.getByRole('button', { name: 'Confirm', exact: true }).click();
	await expect(page).toHaveURL(/\/summary$/);
	await expect
		.poll(() =>
			page.evaluate(
				() =>
					new Promise<string | undefined>((resolve, reject) => {
						const open = indexedDB.open('hbc-scan-recovery', 2);
						open.onerror = () => reject(open.error);
						open.onsuccess = () => {
							const db = open.result;
							const tx = db.transaction('sessions', 'readonly');
							const read = tx.objectStore('sessions').getAll();
							read.onsuccess = () =>
								resolve(read.result[0]?.confirmedItems?.[0]?.custom_fields?.diameter_mm);
							read.onerror = () => reject(read.error);
							tx.oncomplete = () => db.close();
						};
					})
			)
		)
		.toBe('6.2');
	await page.reload();
	await page.getByRole('button', { name: 'Resume Session' }).click();
	await page.getByRole('button', { name: /Submit All Items/ }).click();
	await expect.poll(() => model.submitted.length).toBe(1);
	expect(model.submitted[0]).toMatchObject({
		schema_revision: 0,
		category_id: 'screw',
		custom_fields: { diameter_mm: '6.2', owner_note: 'Keep me' },
	});
});

test('invalid properties stay in Review and do not create an item', async ({ page }) => {
	const model = await installApi(page);
	model.detectedFields.diameter_mm = 'NaN';
	await openReview(page);
	await page.getByRole('button', { name: 'Confirm', exact: true }).click();
	await expect(page.getByRole('alert')).toContainText('Invalid numeric property');
	await expect(page).toHaveURL(/\/review$/);
	await expect(page.getByLabel('Diameter', { exact: true })).toHaveValue('NaN');
	expect(model.submitted).toEqual([]);
});

test('adding a property to a template needs an explicit selection', async ({ page }) => {
	const model = await installApi(page);
	await openReview(page);
	await page.getByLabel('Item category').selectOption('screw');
	await page.getByText('Create a new property', { exact: true }).click();
	await page.getByLabel('Property name', { exact: true }).fill('Thread standard source');
	await page.getByLabel('Also add to the category template').check();
	await page.getByRole('button', { name: 'Preview new property' }).click();
	expect(model.revision).toBe(0);
	await page.getByRole('button', { name: 'Confirm new property' }).click();
	await expect(page.getByLabel('Thread standard source', { exact: true })).toBeVisible();
	const id = Object.keys(model.schema.fields).find(
		(id) => model.schema.fields[id].label === 'Thread standard source'
	)!;
	expect(model.schema.categories.screw.fields).toContain(id);
});
