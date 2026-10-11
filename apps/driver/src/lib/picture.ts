/** Pictures for proof of delivery, made small enough to send from a black
 * spot: a photo becomes a JPEG at most 1600 pixels on its long side (and
 * loses the camera's EXIF, location included); a signature is a PNG. */

export const PHOTO_MAX_SIDE = 1600;

export async function shrinkPhoto(file: Blob, maxSide = PHOTO_MAX_SIDE, quality = 0.8): Promise<Blob> {
  const image = await createImageBitmap(file);
  const scale = Math.min(1, maxSide / Math.max(image.width, image.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(image.width * scale);
  canvas.height = Math.round(image.height * scale);
  canvas.getContext("2d")!.drawImage(image, 0, 0, canvas.width, canvas.height);
  image.close();
  return toBlob(canvas, "image/jpeg", quality);
}

export function toBlob(canvas: HTMLCanvasElement, type: "image/png" | "image/jpeg", quality?: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("That picture couldn't be made. Try again."))), type, quality);
  });
}
