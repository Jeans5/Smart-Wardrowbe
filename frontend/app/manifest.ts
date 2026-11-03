import type { MetadataRoute } from 'next';

// What a tablet or phone uses for the home-screen icon: name, icons, and opening without the
// browser's address bar. Served as /manifest.webmanifest, which the middleware leaves alone
// like every other path with an extension.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Kleerkast',
    short_name: 'Kleerkast',
    description: 'AI-kleerkast met outfitvoorstellen voor het hele gezin',
    start_url: '/',
    scope: '/',
    display: 'standalone',
    background_color: '#3A0F27',
    theme_color: '#3A0F27',
    icons: [
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: '/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  };
}
