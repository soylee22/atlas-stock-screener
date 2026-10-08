// Pages stores stock histories compactly. Also accept a CDN-decoded response.
export async function snapshotJSON(response, compressed = false) {
  if (!response.ok) throw new Error('Company snapshot unavailable');
  if (!compressed) return response.json();
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    return new Response(stream).json();
  }
  return JSON.parse(new TextDecoder().decode(bytes));
}
