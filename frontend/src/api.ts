export interface DrawingDocument {id: string; filename: string; sha256: string; page_count: number}
export interface DocumentLibrary {
  documents: (DrawingDocument & {size_bytes:number})[];
  usage: {documents:number;max_documents:number;bytes:number;max_bytes:number};
}
export async function listPdfs():Promise<DocumentLibrary> {
  return (await check(await fetch('/api/documents',{cache:'no-store'}))).json();
}
export interface NativeImage {
  id: string; document: DrawingDocument; page: number; zoom: number;
  width: number; height: number; blob: Blob; url: string;
}

async function check(response: Response): Promise<Response> {
  if (!response.ok) {
    let detail: unknown;
    try {detail = (await response.json()).detail;} catch {detail = null;}
    throw new Error(typeof detail === "string" ? detail : `요청을 처리하지 못했습니다 (${response.status}).`);
  }
  return response;
}
export async function openWorkspace(): Promise<void> {
  await check(await fetch("/api/workspace", {cache:"no-store"}));
}
export async function uploadPdf(file: File): Promise<DrawingDocument> {
  const form = new FormData(); form.append("file", file);
  return (await check(await fetch("/api/documents", {method: "POST", body: form}))).json();
}
export async function removePdf(document: DrawingDocument, preserveReview = false): Promise<void> {
  await check(await fetch(`/api/documents/${document.id}${preserveReview ? "?preserve_review=1" : ""}`, {method: "DELETE"}));
}
export async function fetchPage(document: DrawingDocument, page: number, zoom: number, signal: AbortSignal) {
  const response = await check(await fetch(`/api/documents/${document.id}/pages/${page}/image?zoom=${zoom}`, {signal}));
  const width = Number(response.headers.get("X-Image-Width"));
  const height = Number(response.headers.get("X-Image-Height"));
  const id = response.headers.get("X-Image-Id");
  if (!id || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0 ||
      response.headers.get("X-Source-SHA256") !== document.sha256 ||
      response.headers.get("X-Page-Number") !== String(page) ||
      response.headers.get("X-Coordinate-System") !== "image_native_pixels") {
    throw new Error("추출 이미지의 원본 좌표 정보를 확인할 수 없습니다.");
  }
  return {id, document, page, zoom, width, height, blob: await response.blob()};
}
