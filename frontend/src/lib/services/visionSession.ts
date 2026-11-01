import { request } from '$lib/api/client';
import { llmProfiles, type VisionReadiness } from '$lib/api/settings';
import { authStore } from '$lib/stores/auth.svelte';
import { collectionStore } from '$lib/stores/collection.svelte';
import { abortSignalAny } from '$lib/utils/abortSignal';

export interface VisionOperation {
	draftId: string;
	revision: number;
	generation: number;
	contextId: string | null;
	collectionId: string | null;
	readiness: VisionReadiness;
}

class VisionSession {
	private id: string | null = null;
	revision = 0;
	private generation = 0;
	private active = new Map<string, { controller: AbortController; operation: VisionOperation }>();

	get draftId(): string {
		return (this.id ??= crypto.randomUUID());
	}

	restore(id: string, revision = 0): void {
		this.invalidate();
		this.id = id;
		this.revision =
			Number.isSafeInteger(revision) && revision >= 0 && revision < 2147483647 ? revision : 0;
	}

	reset(): void {
		this.invalidate();
		this.id = null;
		this.revision = 0;
	}

	invalidate(): void {
		this.generation++;
		for (const active of this.active.values()) {
			active.controller.abort();
		}
	}

	async begin(): Promise<VisionOperation> {
		this.invalidate();
		const generation = this.generation;
		const draftId = this.draftId;
		if (this.revision >= 2147483647) throw new Error('Start a new scan before another analysis.');
		const revision = ++this.revision;
		const contextId = authStore.contextId;
		const collectionId = collectionStore.selectedId;
		const readiness = await llmProfiles.readiness();
		const operation = { draftId, revision, generation, contextId, collectionId, readiness };
		this.check(operation);
		if (!readiness.ready) {
			throw new Error(
				readiness.message ||
					`The selected AI connection is not ready (${readiness.reason || 'not_ready'}).`
			);
		}
		return operation;
	}

	check(operation: VisionOperation): void {
		if (
			operation.generation !== this.generation ||
			operation.draftId !== this.id ||
			operation.contextId !== authStore.contextId ||
			operation.collectionId !== collectionStore.selectedId
		)
			throw new DOMException('The analysis context changed.', 'AbortError');
	}

	private cancelRequest(id: string, operation: VisionOperation): void {
		if (operation.readiness.backend !== 'codex_cli') return;
		void request('/tools/vision/cancel', {
			method: 'POST',
			timeout: 5000,
			skipAuthRetry: true,
			body: JSON.stringify({
				request_id: id,
				draft_id: operation.draftId,
				analysis_revision: operation.revision,
			}),
		}).catch(() => {});
	}

	async run<T>(
		operation: VisionOperation | undefined,
		signal: AbortSignal | undefined,
		send: (
			headers: Record<string, string>,
			signal: AbortSignal,
			timeout: number | undefined
		) => Promise<T>
	): Promise<T> {
		const selected = operation ?? (await this.begin());
		this.check(selected);
		const id = crypto.randomUUID();
		const controller = new AbortController();
		const combined = signal ? abortSignalAny([signal, controller.signal]) : controller.signal;
		const cancel = () => this.cancelRequest(id, selected);
		combined.addEventListener('abort', cancel, { once: true });
		this.active.set(id, { controller, operation: selected });
		try {
			const result = await send(
				{
					'X-Request-ID': id,
					'X-Companion-Draft-ID': selected.draftId,
					'X-Companion-Analysis-Revision': String(selected.revision),
					'X-Companion-Vision-Profile': selected.readiness.profile_revision,
				},
				combined,
				selected.readiness.backend === 'codex_cli'
					? (selected.readiness.timeout_seconds + 15) * 1000
					: undefined
			);
			this.check(selected);
			if (combined.aborted) throw new DOMException('Analysis cancelled.', 'AbortError');
			return result;
		} finally {
			combined.removeEventListener('abort', cancel);
			this.active.delete(id);
		}
	}
}

export const visionSession = new VisionSession();
