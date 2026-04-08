---
name: epub-downloader
description: Search and download free EPUB books from Project Gutenberg, Internet Archive, and Open Library to /ebooks. Triggers on: "download book", "find ebook", "epub", "search book", "download [title]", "get me a book".
version: 1.0.0
metadata: { "openclaw": { "emoji": "📚" } }
---

# Epub Downloader Skill

Search for and download free, legal EPUB books to the `/ebooks` directory.

## Searching for Books

When the user asks to find or search for a book, search these three sources:

### 1. Project Gutenberg (via Gutendex API)

Use `web_fetch` to query:
```
https://gutendex.com/books/?search=<query>
```
Parse the JSON response. For each book in `results`, look for an EPUB link in the `formats` object (keys containing "epub"). Present:
- Title (from `title`)
- Author (from `authors[].name`)
- EPUB URL (from `formats` key containing "epub")
- Source: "Project Gutenberg"

### 2. Internet Archive

Use `web_fetch` to query:
```
https://archive.org/advancedsearch.php?q=<query> AND format:EPUB&output=json&rows=5&fl[]=identifier&fl[]=title&fl[]=creator
```
For each result in `response.docs`, the EPUB download URL is:
```
https://archive.org/download/<identifier>/<identifier>.epub
```
Present:
- Title (from `title`)
- Author (from `creator`)
- Source: "Internet Archive"

### 3. Open Library

Use `web_fetch` to query:
```
https://openlibrary.org/search.json?q=<query>&limit=5&has_fulltext=true
```
Filter results where `ebook_access` is "public" or "borrowable".
The EPUB URL pattern is:
```
https://openlibrary.org<key>.epub
```
Present:
- Title (from `title`)
- Author (from `author_name[]`)
- Source: "Open Library"

## Presenting Results

Combine results from all three sources and present as a numbered list:

```
📚 Found X free EPUBs for "<query>":

1. 📖 The Art of War by Sun Tzu — Project Gutenberg
2. 📖 The Art of War by Sun Tzu — Internet Archive
3. 📖 The Art of War (translated) by Lionel Giles — Open Library
```

## Downloading Books

When the user selects a book (e.g., "download #1"), use `web_fetch` to download the EPUB file, then save it to `/ebooks/`.

Use `exec` to download with curl:
```bash
curl -L -o "/ebooks/<sanitized_title>.epub" "<epub_url>"
```

Sanitize the title for the filename: replace spaces with underscores, remove special characters.

After downloading, verify the file exists and show its size:
```bash
ls -lh "/ebooks/<sanitized_title>.epub"
```

Confirm to the user:
```
✅ Downloaded: <Title> by <Author>
📁 Saved to: /ebooks/<filename>.epub
📦 Size: <size>
🔄 Will sync to OneDrive automatically
```

## Important Rules

1. ONLY download from the three supported sources (Gutenberg, Internet Archive, Open Library)
2. All books from these sources are public domain or openly licensed
3. Always show the source of each book
4. The `/ebooks` directory maps to the user's Windows OneDrive Ebooks folder
5. If a download fails, suggest trying an alternate source
6. If `curl` is not available, use `wget -O` instead
7. Sanitize filenames: replace spaces with `_`, remove characters not in `[a-zA-Z0-9_-]`