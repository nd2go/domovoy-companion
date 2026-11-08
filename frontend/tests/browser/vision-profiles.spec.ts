import { expect, test, type Page, type Route } from '@playwright/test';

const json = (body: unknown, status = 200) => ({
	status,
	contentType: 'application/json',
	body: JSON.stringify(body),
});
const photo = {
	name: 'control.png',
	mimeType: 'image/png',
	buffer: Buffer.from(
		'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
		'base64'
	),
};

async function setup(
	page: Page,
	options: {
		detect?: (route: Route) => Promise<void>;
		correct?: (route: Route) => Promise<void>;
	} = {}
) {
	const calls: { path: string; headers: Record<string, string>; body: unknown }[] = [];
	const profiles: Record<string, unknown>[] = [];
	const draftRevisions = new Map<string, number>();
	await page.route('**/api/**', async (route) => {
		const request = route.request();
		const path = new URL(request.url()).pathname;
		if (path === '/api/drafts' && request.method() === 'GET') return route.fulfill(json([]));
		if (path === '/api/drafts' && request.method() === 'POST') {
			const id = request.postDataJSON().id as string;
			const revision = draftRevisions.get(id) ?? 0;
			draftRevisions.set(id, revision);
			return route.fulfill(json({ id, revision, photos: [], state: {} }, 201));
		}
		const draftMatch = path.match(/^\/api\/drafts\/([^/]+)(?:\/photos\/([^/]+))?$/);
		if (draftMatch && request.method() === 'PUT')
			return route.fulfill(json({ id: draftMatch[2], checksum: 'synthetic', size: 1 }));
		if (draftMatch && request.method() === 'PATCH') {
			const revision = (draftRevisions.get(draftMatch[1]) ?? 0) + 1;
			draftRevisions.set(draftMatch[1], revision);
			return route.fulfill(
				json({ id: draftMatch[1], revision, photos: [], state: request.postDataJSON().state })
			);
		}
		calls.push({ path, headers: request.headers(), body: request.postData() });
		if (path === '/api/config')
			return route.fulfill(
				json({
					auth_mode: 'api_key',
					is_demo_mode: false,
					demo_mode_explicit: false,
					homebox_url: 'http://homebox.test',
					llm_model: '',
					vision_backend: 'codex_cli',
					update_check_enabled: false,
					image_quality: 'raw',
					log_level: 'INFO',
					capture_max_images: 30,
					capture_max_file_size_mb: 20,
					print_enabled: false,
				})
			);
		if (path === '/api/homebox/connection')
			return route.fulfill(
				json({
					connected: true,
					context_id: 'deployment:user',
					user_id: 'owner',
					default_group_id: 'g1',
				})
			);
		if (path === '/api/groups')
			return route.fulfill(
				json([
					{ id: 'g1', name: 'Personal' },
					{ id: 'g2', name: 'Shared' },
				])
			);
		if (path === '/api/locations/tree')
			return route.fulfill(json([{ id: 'loc-1', name: 'Storage', itemCount: 0, children: [] }]));
		if (path === '/api/locations/loc-1')
			return route.fulfill(json({ id: 'loc-1', name: 'Storage', itemCount: 0, children: [] }));
		if (path === '/api/version')
			return route.fulfill(
				json({ version: 'test', latest_version: null, update_available: false })
			);
		if (path === '/api/tags') return route.fulfill(json([]));
		if (path === '/api/field-preferences') return route.fulfill(json({ default_tag_id: null }));
		if (path === '/api/settings/custom-fields') return route.fulfill(json([]));
		if (path === '/api/llm/readiness')
			return route.fulfill(
				json({
					backend: 'codex_cli',
					model: null,
					ready: true,
					reason: null,
					profile_revision: 'reviewed-codex-profile',
					timeout_seconds: 120,
					max_parallelism: 1,
					chat_available: false,
				})
			);
		if (path === '/api/llm/profiles') {
			if (request.method() === 'POST') {
				const profile = {
					...request.postDataJSON(),
					status: 'primary',
					has_api_key: false,
					api_base: null,
				};
				profiles.push(profile);
				return route.fulfill(json(profile, 201));
			}
			return route.fulfill(json({ profiles }));
		}
		if (path === '/api/tools/vision/detect') {
			if (options.detect) return options.detect(route);
			return route.fulfill(
				json({
					items: [{ name: 'Control item', quantity: 1, model_number: 'CONTROL' }],
					compressed_images: [],
				})
			);
		}
		if (path === '/api/tools/vision/correct' && options.correct) return options.correct(route);
		if (path === '/api/tools/vision/cancel') return route.fulfill(json({ cancelled: true }));
		return route.fulfill(json({}));
	});
	return { calls, profiles };
}

async function capture(page: Page, count = 1) {
	await page.goto('/location');
	await page.getByRole('button', { name: /Storage/ }).click();
	await page.getByRole('button', { name: 'Select current location' }).click();
	await page.getByRole('button', { name: 'Continue to Capture' }).click();
	await page
		.locator('input[type="file"]')
		.first()
		.setInputFiles(
			Array.from({ length: count }, (_, index) => ({ ...photo, name: `control-${index}.png` }))
		);
}

test('Codex profile has a separate connection and needs no API key or model', async ({ page }) => {
	const state = await setup(page);
	await page.goto('/settings');
	await page.getByRole('button', { name: 'Add', exact: true }).click();
	await page.getByLabel('Name', { exact: true }).fill('My Codex');
	await page.getByLabel('Connection', { exact: true }).selectOption('codex_cli');
	await expect(page.getByLabel('API Key', { exact: true })).toHaveCount(0);
	await expect(page.getByLabel('Model', { exact: true })).toHaveValue('');
	await page
		.locator('form')
		.filter({ has: page.getByLabel('Connection', { exact: true }) })
		.getByRole('button', { name: 'Save', exact: true })
		.click();
	await expect(page.getByText('My Codex', { exact: true })).toBeVisible();
	expect(state.profiles[0]).toMatchObject({ backend: 'codex_cli', model: '' });
	await expect(page.getByRole('status').filter({ hasText: 'Codex CLI' })).toContainText('Ready');
});

test('CLI chat explains its limit without sending a chat request', async ({ page }) => {
	const state = await setup(page);
	await page.goto('/chat');
	await expect(page.getByRole('heading', { name: 'Chat Unavailable' })).toBeVisible();
	await expect(
		page.getByText('A Codex profile supports photo analysis. Select an API profile to use chat.')
	).toBeVisible();
	expect(state.calls.some((call) => call.path === '/api/chat/messages')).toBe(false);
});

test('Codex photo requests run sequentially with one draft and distinct request IDs', async ({
	page,
}) => {
	let active = 0;
	let maximum = 0;
	const state = await setup(page, {
		detect: async (route) => {
			active++;
			maximum = Math.max(maximum, active);
			await new Promise((resolve) => setTimeout(resolve, 80));
			await route.fulfill(
				json({ items: [{ name: 'Control item', quantity: 1 }], compressed_images: [] })
			);
			active--;
		},
	});
	await capture(page, 2);
	await page.getByRole('button', { name: /Analyze with AI/ }).click();
	await expect(page).toHaveURL(/\/review$/);
	const sent = state.calls.filter((call) => call.path === '/api/tools/vision/detect');
	expect(sent).toHaveLength(2);
	expect(maximum).toBe(1);
	expect(sent[0].headers['x-companion-draft-id']).toBe(sent[1].headers['x-companion-draft-id']);
	expect(sent[0].headers['x-companion-analysis-revision']).toBe(
		sent[1].headers['x-companion-analysis-revision']
	);
	expect(sent[0].headers['x-request-id']).not.toBe(sent[1].headers['x-request-id']);
});

test('worker failure keeps the Homebox session, manual edits, and correction instructions', async ({
	page,
}) => {
	await setup(page, {
		correct: (route) =>
			route.fulfill(json({ code: 'VISION_PROVIDER_FAILED', detail: 'Codex is unavailable' }, 502)),
	});
	await capture(page);
	await page.getByRole('button', { name: /Analyze with AI/ }).click();
	await expect(page).toHaveURL(/\/review$/);
	await page.getByLabel('Name', { exact: true }).fill('Owner edit');
	await page.getByRole('button', { name: 'AI Correction', exact: true }).click();
	const prompt = page.getByPlaceholder(/The brand is Sony/);
	await prompt.fill('Keep my name and check the model');
	await page.getByRole('button', { name: 'Correct with AI', exact: true }).click();
	await expect(page.getByText('Codex is unavailable', { exact: true })).toBeVisible();
	await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Owner edit');
	await expect(prompt).toHaveValue('Keep my name and check the model');
	await expect(page.getByRole('button', { name: 'Confirm', exact: true })).toBeEnabled();
	await expect(page).toHaveURL(/\/review$/);
});

test('cancel sends the exact active context and leaves photos for a manual retry', async ({
	page,
}) => {
	let release!: () => void;
	const held = new Promise<void>((resolve) => {
		release = resolve;
	});
	const state = await setup(page, {
		detect: async (route) => {
			await held;
			await route
				.fulfill(json({ items: [{ name: 'Late item', quantity: 1 }], compressed_images: [] }))
				.catch(() => {});
		},
	});
	await capture(page);
	await page.getByRole('button', { name: /Analyze with AI/ }).click();
	await expect
		.poll(() => state.calls.filter((call) => call.path === '/api/tools/vision/detect').length)
		.toBe(1);
	await page.getByRole('button', { name: 'Cancel Analysis', exact: true }).click();
	await expect
		.poll(() => state.calls.filter((call) => call.path === '/api/tools/vision/cancel').length)
		.toBeGreaterThan(0);
	const sent = state.calls.find((call) => call.path === '/api/tools/vision/detect')!;
	const cancelled = JSON.parse(
		state.calls.find((call) => call.path === '/api/tools/vision/cancel')!.body as string
	);
	expect(cancelled).toEqual({
		request_id: sent.headers['x-request-id'],
		draft_id: sent.headers['x-companion-draft-id'],
		analysis_revision: Number(sent.headers['x-companion-analysis-revision']),
	});
	release();
	await expect(page.getByRole('button', { name: /Analyze with AI/ })).toBeVisible();
	await expect(page).toHaveURL(/\/capture$/);
});

test('collection switch discards a late CLI result', async ({ page }) => {
	let release!: () => void;
	const held = new Promise<void>((resolve) => {
		release = resolve;
	});
	const state = await setup(page, {
		detect: async (route) => {
			await held;
			await route
				.fulfill(
					json({ items: [{ name: 'Foreign late result', quantity: 1 }], compressed_images: [] })
				)
				.catch(() => {});
		},
	});
	await capture(page);
	await page.getByRole('button', { name: /Analyze with AI/ }).click();
	await expect
		.poll(() => state.calls.filter((call) => call.path === '/api/tools/vision/detect').length)
		.toBe(1);
	await page.locator('#collection-selector').first().click();
	await page.getByRole('option', { name: /Shared/ }).click();
	release();
	await expect(page.getByText('Foreign late result', { exact: true })).toHaveCount(0);
	await expect(page).toHaveURL(/\/capture$/);
	await expect(page.getByRole('button', { name: /Analyze with AI/ })).toBeDisabled();
	await expect(page.getByText('0 / 30 images · 20MB per file')).toBeVisible();
});
