# API Map — topic → authoritative documentation

Use this the moment a task exceeds what the stack documents. Fetch the URL, quote the real field names, then plan. Do not answer Telegram capability questions from memory.

## Tier 0 — always fetchable, always current

| Need | URL |
|---|---|
| Any method or type signature | <https://core.telegram.org/bots/api> |
| "Is this new? does it exist yet?" | <https://core.telegram.org/bots/api-changelog> |
| Official UX canon for bots | <https://core.telegram.org/bots/features> |
| Announcements before docs update | <https://t.me/botnews> |

The main API page is one long document with stable anchors. `#sendmessage`, `#inlinekeyboardmarkup`, `#formatting-options`, `#stickers`, `#payments`, `#inline-mode`, `#available-methods`, `#update`. Anchor = lowercased method/type name with no separators.

## Tier 1 — by domain

### Messaging and formatting
| Topic | URL |
|---|---|
| Formatting options, MarkdownV2 reserved chars, HTML tags | `bots/api#formatting-options` |
| `MessageEntity` types incl. `custom_emoji`, `date_time` | `bots/api#messageentity` |
| `ReplyParameters`, quoting a fragment | `bots/api#replyparameters` |
| Forward vs copy semantics | `bots/api#forwardmessage`, `bots/api#copymessage` |
| Reactions | `bots/api#setmessagereaction`, `bots/api#reactiontype` |
| Chat actions ("typing", "upload_photo", …) | `bots/api#sendchataction` |
| Message drafts / streaming partial text | `bots/api#sendmessagedraft` |

### Rich Messages (10.1+) — the modern surface for long/AI answers
| Topic | URL |
|---|---|
| Send a structured rich message | `bots/api#sendrichmessage` |
| Stream a rich message (LLM typing effect) | `bots/api#sendrichmessagedraft` |
| Block model: paragraphs, headings, tables, lists, quotes, code, math, maps, collages, slideshows | `bots/api#inputrichmessage` and the `InputRichBlock*` types |
| Media inside rich messages | `bots/api#inputrichmessagemedia`, `bots/api#inputmediavoicenote` |

### Ephemeral / group hygiene (10.2+)
| Topic | URL |
|---|---|
| Message visible to one user in a group | `bots/api#sendmessage` → `receiver_user_id`; `bots/api#editephemeralmessagetext`; `bots/api#deleteephemeralmessage` |
| Communities | `bots/api#community` |

### Keyboards and navigation
| Topic | URL |
|---|---|
| Inline keyboard, all button kinds | `bots/api#inlinekeyboardbutton` |
| Custom emoji icon + style on buttons (9.4+) | `bots/api#inlinekeyboardbutton` → `icon_custom_emoji_id`, `style` |
| Reply keyboard, request-user/chat buttons | `bots/api#replykeyboardmarkup`, `bots/api#keyboardbuttonrequestusers` |
| Pre-saved keyboard buttons (9.6+) | `bots/api#savepreparedkeyboardbutton` |
| Commands, scopes, localization | `bots/api#setmycommands`, `bots/api#botcommandscope` |
| Menu button | `bots/api#setchatmenubutton` |
| Deep links | `bots/features#deep-linking` |
| Inline mode | <https://core.telegram.org/bots/inline> |

### Stickers and custom emoji
| Topic | URL |
|---|---|
| Format specs, dimensions, duration, FPS, weight | <https://core.telegram.org/stickers> |
| Bot-side set management | `bots/api#stickers` — `uploadStickerFile`, `createNewStickerSet`, `addStickerToSet`, `replaceStickerInSet`, `setStickerEmojiList`, `setStickerKeywords`, `setStickerSetThumbnail`, `deleteStickerFromSet`, `getCustomEmojiStickers` |
| MTProto-side internals, set flags | <https://core.telegram.org/api/stickers> |
| Custom emoji semantics, entity rules | <https://core.telegram.org/api/custom-emoji> |

### Mini Apps
| Topic | URL |
|---|---|
| Bot-API side: `WebAppInfo`, `web_app` buttons, `sendData`, `answerWebAppQuery` | <https://core.telegram.org/bots/webapps> |
| Client JS API: methods, events, theme, viewport, safe area, fullscreen | <https://core.telegram.org/bots/webapps#initializing-mini-apps> and <https://docs.telegram-mini-apps.com/platform/methods> |
| Haptics | <https://docs.telegram-mini-apps.com/platform/haptic-feedback> |
| initData structure and validation algorithm | <https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app> |

### Payments and Stars
| Topic | URL |
|---|---|
| Invoices, pre-checkout, successful payment | <https://core.telegram.org/bots/payments> |
| Stars economics, balances, withdrawal | <https://core.telegram.org/api/stars> |
| Star subscriptions | `bots/api#createinvoicelink` → `subscription_period`; `bots/api#edituserstarsubscription` |
| Refunds | `bots/api#refundstarpayment` |
| Balance | `bots/api#getmystarbalance` |
| Gifts | `bots/api#sendgift`, `bots/api#getuserGifts`, `bots/api#upgradegift`, `bots/api#transfergift` |

### Business, checklists, posts, stories
| Topic | URL |
|---|---|
| Business accounts and rights | `bots/api#businessbotrights` |
| Checklists (9.1+) | `bots/api#sendchecklist`, `bots/api#editmessagechecklist` |
| Suggested posts (9.2+) | `bots/api#approvesuggestedpost` |
| Direct messages topics (9.2+) | `bots/api#directmessagestopic` |
| Stories (9.0+) | `bots/api#poststory` |
| Managed bots (9.6+) | `bots/api#getmanagedbottoken` |
| Guest mode (10.0+) | `bots/api#answerguestquery` |

### Infrastructure
| Topic | URL |
|---|---|
| Webhooks: setup, secret token, IP, retries | <https://core.telegram.org/bots/webhooks> |
| Local Bot API server (large files, no rate limits) | <https://github.com/tdlib/telegram-bot-api> |
| FAQ, limits folklore, "why is my bot slow" | <https://core.telegram.org/bots/faq> |

## Tier 2 — framework docs (only after the API is understood)

| Framework | URL |
|---|---|
| aiogram 3.x | <https://docs.aiogram.dev/> |
| python-telegram-bot | <https://docs.python-telegram-bot.org/> |
| grammY (+ runner, plugins) | <https://grammy.dev/> |
| Telegraf | <https://telegraf.js.org/> |
| teloxide | <https://docs.rs/teloxide/> |
| Framework comparison, honest | <https://grammy.dev/resources/comparison> |
| Mini Apps SDK (React) | <https://www.npmjs.com/package/@telegram-apps/sdk-react> |

## Rule of precedence

API docs > changelog > framework docs > this stack > blog posts > memory.

When the framework lags the API — common for 10.x — do not wait. Use the raw HTTP escape hatch documented in `telegram-backend`, and note in the code which API version introduced the method.
