const storedImage = /^\/images\/([a-f0-9]{64}\.(?:jpg|jpeg|png|webp|gif|bmp|tiff?))$/

/** Keep sample and external URLs intact. Only immutable library originals have previews. */
export function thumbnailSrc(src: string) {
  const match = storedImage.exec(src)
  return match ? `/images/thumb-v1/${match[1]}` : src
}
