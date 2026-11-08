import { request, requestBlobUrl } from './client';
import type { StoredSession } from '$lib/services/serialize';

export interface DraftPhoto {
	id: string;
	filename: string;
	mime: string;
	size: number;
	checksum: string;
}

export interface ServerDraft {
	id: string;
	revision: number;
	state: Partial<StoredSession>;
	created_at: number;
	updated_at: number;
	photos: DraftPhoto[];
}

export const drafts = {
	create: (id: string) =>
		request<ServerDraft>('/drafts', { method: 'POST', body: JSON.stringify({ id }) }),
	list: () => request<ServerDraft[]>('/drafts'),
	read: (id: string) => request<ServerDraft>(`/drafts/${id}`),
	update: (id: string, revision: number, state: Partial<StoredSession>) =>
		request<ServerDraft>(`/drafts/${id}`, {
			method: 'PATCH',
			body: JSON.stringify({ revision, state }),
		}),
	upload: (draftId: string, photoId: string, file: File) => {
		const form = new FormData();
		form.append('file', file);
		return request<DraftPhoto>(`/drafts/${draftId}/photos/${photoId}`, {
			method: 'PUT',
			body: form,
			timeout: 120_000,
		});
	},
	original: async (draftId: string, photoId: string, photo: DraftPhoto) => {
		const result = await requestBlobUrl(`/drafts/${draftId}/photos/${photoId}/original`);
		try {
			const blob = await (await fetch(result.url)).blob();
			return new File([blob], photo.filename, { type: photo.mime });
		} finally {
			result.revoke();
		}
	},
	derivedUpload: async (draftId: string, dataUrl: string) => {
		const blob = await (await fetch(dataUrl)).blob();
		const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
		const checksum = Array.from(new Uint8Array(digest), (byte) =>
			byte.toString(16).padStart(2, '0')
		).join('');
		const form = new FormData();
		form.append('file', blob, 'derived');
		await request(`/drafts/${draftId}/derived/${checksum}`, {
			method: 'PUT',
			body: form,
			timeout: 120_000,
		});
		return `draft-derived:${checksum}`;
	},
	derived: async (draftId: string, reference: string) => {
		if (!reference.startsWith('draft-derived:')) return reference;
		const checksum = reference.slice('draft-derived:'.length);
		const result = await requestBlobUrl(`/drafts/${draftId}/derived/${checksum}`);
		try {
			const blob = await (await fetch(result.url)).blob();
			return await new Promise<string>((resolve, reject) => {
				const reader = new FileReader();
				reader.onload = () => resolve(reader.result as string);
				reader.onerror = () => reject(reader.error);
				reader.readAsDataURL(blob);
			});
		} finally {
			result.revoke();
		}
	},
	remove: (id: string) => request<void>(`/drafts/${id}`, { method: 'DELETE' }),
};
