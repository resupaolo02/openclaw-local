````skill
---
name: media-downloader
description: Use when the user wants to download or find ebooks, EPUB files, PDFs, audiobooks, or other digital media. Routes to the correct sub-skill based on media type. For ebooks/EPUBs: use the epub-downloader skill. Triggers on: "download book", "find ebook", "epub", "PDF book", "audiobook download", "find media", "search book".
version: 1.0.0
metadata: { "openclaw": { "emoji": "📥" } }
---

# Media Downloader

Central skill for all digital media downloads. Routes to the right sub-skill based on what the user wants.

## Routing

| Request type | Skill to use |
|---|---|
| Ebook / EPUB / public domain book | `epub-downloader` |
| PDF document | `epub-downloader` (also checks Internet Archive for PDFs) |
| Audiobook | `epub-downloader` (Internet Archive has audiobooks) |
| Other media | Use `web_search` to find legal free sources |

## epub-downloader

Search and download free, legal EPUB/PDF books from:
- **Project Gutenberg** (gutendex.com API) — public domain classics
- **Internet Archive** (archive.org) — vast collection including modern books under controlled digital lending
- **Open Library** (openlibrary.org) — catalog + borrowable ebooks

Download location: `/ebooks/` volume

Invoke the `epub-downloader` skill directly for the actual search and download steps.

## Notes

- All sources are free and legal
- `/ebooks` is mounted from the host at `/mnt/c/Users/resup/OneDrive .../Ebooks`
- If a copyrighted book is requested, explain legal availability and suggest library alternatives
````
