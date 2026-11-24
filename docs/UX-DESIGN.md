# Hellbird UX design

How Helium's ideas map onto Ladybird `2026.47.7` (`4e9acf4c02ca23239fe32224a303dcea4614562b`). Patches live in
`patches/privacy`, `patches/features` and `patches/ui`, and apply in that order after `patches/build`.

## Privacy defaults

| Behaviour | Ladybird | Hellbird | Patch |
|---|---|---|---|
| Global Privacy Control (`Sec-GPC: 1`) | Off | On | `privacy/0001` |
| Content blocking | Off | On, with EasyList and EasyPrivacy chosen | `privacy/0001` |
| Filter list downloads | On the welcome page's switch | Only after finishing the welcome page with blocking on, or from settings | `privacy/0001` |
| Remote search suggestions | Off, but on once an engine is picked on the welcome page | Off until switched on | `privacy/0001` |
| Crash reports | Kept, and asks | Never kept or sent | `privacy/0001` |
| Feedback button | Shown | Hidden | `privacy/0001` |
| Report server | `reports.app.ladybird.org` | None, unless `LADYBIRD_REPORTS_ENDPOINT` is set | `privacy/0002` |
| "Report Website Issue" items | Shown | Hidden without a report server | `privacy/0002` |
| Automatic update checks | On, counting installations | Off, and the request no longer describes the installation | `privacy/0003` |
| Default bookmarks | Three project links | None | `privacy/0004` |

Content blocking has no rules until its lists are downloaded, because Ladybird ships none. No list is fetched before the
user has seen the switch that says so. Once enabled there, lists update daily.

The update feed is still Ladybird's, so it cannot announce Hellbird releases. Builds should also set
`LADYBIRD_ENABLE_UPDATE_CHECK=OFF`, which removes the feature from the UI. Saved settings always win over defaults.

## Search

DuckDuckGo is the default. Engines are offered in this order: DuckDuckGo, Kagi, Startpage, Qwant, Brave Search, Ecosia,
Mojeek, Wikipedia, Google, Bing, Yahoo, Yandex. Choosing no engine is remembered, and is not replaced by the default.

### Native !bangs

Helium downloads its list; Hellbird's is built in, so resolving a bang needs no network and no search engine.

- `!gh ladybird`, `ladybird !gh`, and `!gh` alone for the home page. Triggers ignore case.
- A bang at the start wins over one at the end. Unknown bangs are searched for as typed.
- Only text that would be searched for can be a bang, so no address changes meaning. With Ctrl+Return, text is an address.
- The query is percent-encoded with the URL component set.
- The omnibox row says where it goes ("Search GitHub"), and bang text is never sent for remote suggestions.
- Settings > Search > Use !bangs turns them off (`searchEngine.bangs`). They work without a search engine.

| Site | Bangs |
|---|---|
| Google | `!g`, `!google` |
| Google Images | `!gi` |
| DuckDuckGo | `!ddg`, `!d` |
| Kagi | `!k`, `!kagi` |
| Startpage | `!sp`, `!startpage` |
| Brave Search | `!br`, `!brave` |
| Qwant | `!q`, `!qwant` |
| Ecosia | `!ec`, `!ecosia` |
| Mojeek | `!mojeek` |
| Bing | `!b`, `!bing` |
| Wikipedia | `!w`, `!wiki` |
| Wiktionary | `!wt` |
| Wolfram Alpha | `!wa` |
| Internet Archive | `!ia` |
| Wayback Machine | `!wb` |
| IMDb | `!imdb` |
| arXiv | `!arxiv` |
| Google Scholar | `!scholar`, `!gs` |
| GitHub | `!gh`, `!github` |
| GitLab | `!gl`, `!gitlab` |
| Codeberg | `!cb` |
| Stack Overflow | `!so` |
| Stack Exchange | `!se` |
| MDN Web Docs | `!mdn` |
| Can I use | `!ciu` |
| npm | `!npm` |
| JSR | `!jsr` |
| PyPI | `!pypi` |
| Python Documentation | `!py` |
| crates.io | `!cr`, `!crates` |
| Docs.rs | `!docsrs` |
| Rust Standard Library | `!rust` |
| Go Packages | `!go` |
| RubyGems | `!gem` |
| NuGet | `!nuget` |
| Maven Central | `!mvn` |
| Docker Hub | `!docker` |
| cppreference | `!cpp` |
| Qt Documentation | `!qt` |
| Microsoft Learn | `!msdn` |
| Arch manual pages | `!man` |
| ArchWiki | `!arch`, `!aw` |
| Arch Linux Packages | `!archpkg` |
| Arch User Repository | `!aur` |
| Debian Packages | `!deb` |
| Flathub | `!fh` |
| NixOS Packages | `!nix` |
| Homebrew Formulae | `!brew` |
| Hugging Face | `!hf` |
| YouTube | `!yt`, `!youtube` |
| Twitch | `!tw` |
| Spotify | `!spotify` |
| SoundCloud | `!sc` |
| Bandcamp | `!bc` |
| Steam | `!steam` |
| ProtonDB | `!pdb` |
| Reddit | `!r`, `!reddit` |
| Hacker News | `!hn` |
| Lobsters | `!lob` |
| X | `!x` |
| Bluesky | `!bsky` |
| Google Maps | `!maps`, `!gm` |
| OpenStreetMap | `!osm` |
| Google Translate | `!tr` |
| DeepL | `!deepl` |
| Amazon | `!a`, `!amazon` |
| eBay | `!e`, `!ebay` |

The table is `Libraries/LibWebView/Bang.cpp`.

## Shortcuts and tabs

| Action | Windows and Linux | Note |
|---|---|---|
| Copy page URL | Ctrl+Shift+C | New. Was a third shortcut for DevTools, which keep F12 and Ctrl+Shift+I |
| Copy URL | Tab menu | Was "Copy Link" |
| Close Tabs to the Left / Above | Tab menu | New. Pinned tabs stay open |
| Close other, left or right tabs | Tab menu | Disabled when there is nothing to close |
| Focus the new tab page's field | `/` | Escape clears it |

Already in Ladybird and left alone: Duplicate Tab, Mute Tab, Pin Tab, Reopen Closed Tab (Ctrl+Shift+T), Search Tabs
(Ctrl+Shift+A), vertical tabs.

## Look

Flat warm-gray surfaces, hairlines instead of shadows, and one accent. The Ember theme is the default: its surfaces are
neutral, only highlights are ember, and selected rows are gray. Ladybird's themes remain, including System, which
follows the system's accent.

| Token | Light | Dark |
|---|---|---|
| Tab strip | `#e6e5e3` | `#121211` |
| Toolbar, active tab | `#fcfbfa` | `#222120` |
| Page | `#ffffff` | `#1a1918` |
| Panel | `#fdfcfb` | `#2a2927` |
| Text | `#1c1b1a` | `#f1efed` |
| Muted text | `#605d59` | `#aba8a3` |
| Hairline | `#dddbd8` | `#3b3937` |
| Accent base | `#d95a1e` | `#d95a1e` |
| Link | `#b3430f` | `#ff8a4c` |
| Selected row | `#575450` | `#4a4743` |

The accent base is darkened or lightened by Ladybird's own contrast steps where it meets text or the toolbar. Private
windows keep a violet cast, less saturated. Increased contrast palettes are unchanged.

Layout: the toolbar is 38 px (was 40), the open sidebar 228 px (was 236), the address field's corners 8 px, menus 8 px
with 5 px rows, and cards on pages 10 px.

## Pages

- `about:newtab`: the mark, one field and one line of hints. It was a static page, and is now a WebUI (`NewTabUI`),
  because the field resolves text in the browser, exactly like the address field. The page only learns the engine's name
  and whether bangs are on. The address field still takes focus in a new tab.
- `about:version`: Hellbird's and the engine's versions, and the live privacy status. Versions come from
  `HELLBIRD_VERSION` and `HELLBIRD_ENGINE_VERSION`, which a build may define.
- `about:settings`, `about:welcome`: the palette, the Ember swatch and the bangs switch.

## Naming

What the user reads says Hellbird. These keep the engine's name on purpose: the user agent, profile paths, IPC and object
names, the desktop file ID `org.ladybird.Ladybird`, environment variables, and log output.

## Upstream tests that expect Ladybird's defaults

`Tests/` is not patched. Expect differences in tests of default settings (GPC, content blocking, crash reports, update
checks, search engine), in `test-webdriver-search-engine-settings.py` ("Brave" is now "Brave Search"), in
`TestNotificationFreedesktopArguments` (application name), and in tests of Ctrl+Shift+C. Test mode keeps GPC off.
