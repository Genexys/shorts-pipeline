# Cross-posting to Instagram Reels

A video that has uploaded to YouTube can also be published as an Instagram
Reel. It is off by default, it runs only after a successful YouTube upload, and
a failure never fails the job — by that point the video is already live.

## Why this chain

Meta offers two ways in, and they are not equivalent for our purpose.

**Instagram API with Facebook Login** (what this uses) can push the file's bytes
straight to `rupload.facebook.com`. It needs a Facebook Page connected to the
Instagram account.

**Instagram API with Instagram Login** needs no Page, but it cannot accept
bytes at all: it takes a `video_url` that Meta fetches, which would mean
standing up public file hosting for videos that already sit on this disk.

No App Review is needed either way. Meta requires it only for apps acting on
behalf of *other* people's accounts; publishing to your own runs on Standard
Access.

## One-time setup

1. **An Instagram professional account.** Creator or Business, either works.
   A personal account cannot be published to through the API at all.
2. **A Facebook Page.** A Page, not a personal profile. Free to create.
3. **Connect them**, from the Instagram account's settings. This is the step
   everything else depends on: the Graph API finds the Instagram account by
   asking the Page for its `instagram_business_account`, so without the
   connection the account is invisible no matter how the app is configured.
4. **An app at developers.facebook.com**, type Business. Copy its App ID and
   App Secret into `.env` as `META_APP_ID` and `META_APP_SECRET`.
5. Add `https://localhost/` (or whatever `INSTAGRAM_REDIRECT_URI` is set to) to
   the app's **Valid OAuth Redirect URIs**.
6. Run the auth script on a machine with a browser:

   ```bash
   uv run python Backend/instagram_auth.py
   ```

   It prints a login URL, takes the redirected URL back (the page will fail to
   load — that is expected, the code is in the address bar), exchanges it for a
   long-lived Page token, finds the Instagram account, and writes
   `Backend/instagram_token.json`.

7. Copy that file to `secrets/` on the machine running the worker, and set
   `INSTAGRAM_CROSSPOST="true"`.

The Page token it saves is derived from a long-lived User token and therefore
carries **no expiry date**, so unlike the YouTube path there is no refresh step
that can fail mid-upload.

## What gets published

The caption is built from the YouTube metadata, with three changes:

- **The title leads.** A Reel has no title field, so the best line in the video
  would otherwise be thrown away.
- **The Sources block is dropped.** Links are not clickable in an Instagram
  caption; five bare URLs cost a few hundred characters and buy nothing. The
  sources still stand on the YouTube description of the same video.
- **`#Shorts` becomes `#Reels`.** It names the wrong platform's format, and a
  tag nobody searches is a wasted slot out of thirty.

Meta's limits: 2200 characters, 30 hashtags, 20 `@` mentions. When the caption
has to be cut, prose goes before tags — the tags are how the post is found.

## The four calls

```
1. create a container   graph.facebook.com    Authorization: Bearer
2. push the bytes       rupload.facebook.com  Authorization: OAuth
3. poll until FINISHED  graph.facebook.com    Authorization: Bearer
4. publish              graph.facebook.com    Authorization: Bearer
```

Step 2 is the odd one: a different host, and `OAuth` where the rest take
`Bearer`. Meta documents both spellings and they are not interchangeable.

Despite the name, "resumable" describes only the endpoint. Meta documents no
way to ask how many bytes it already holds and no `Content-Range`, so an
interrupted upload cannot be continued — the next attempt starts a new
container.

## Video requirements

`video.py` already satisfies these for every render, but they are the reason
two of its encoder settings exist:

| Requirement | Where it is met |
|---|---|
| AAC, 48 kHz maximum | `DELIVERY_AUDIO_ARGS` — `loudnorm` otherwise leaves 96 kHz |
| moov atom at the front | `DELIVERY_CONTAINER_ARGS` — ffmpeg writes it last by default |
| H.264, 23–60 FPS, ≤1920 px wide | the encoder arguments and the 1080×1920 format |
| 3 s to 15 min, ≤300 MB | checked before the upload spends a container |

## Rate limits

Meta's own pages give the daily publish cap as both 50 and 100, so
`publishing_quota()` asks the account instead of picking one. At four videos a
day neither number is close.

Worth knowing: Meta demotes accounts posting repetitive or unoriginal content,
and four near-identical uploads a day is what that looks like from outside.
`crosspostInstagram` can be set per job so the autopilot can pick which videos
are worth a Reel.
