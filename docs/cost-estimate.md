# Cost estimate

Prices were fetched from the AWS Price List API for **us-east-1 on 2026-09-18**, with `scripts/price_lookup.py`
(commands at the bottom). These are **estimates**: they exclude taxes and any free-tier credits, and where a line
item could not be retrieved or an interpretation is mine, it is marked. Nothing here is a quote.

## Assumptions

- Single-pipeline MediaLive channel, three renditions: 1080p at 5 Mbps, 720p at 3 Mbps, 480p at 1.5 Mbps, 30 fps,
  plus one AAC audio track at 128 kbps. The source is about 6.4 Mbps SRT.
- MediaLive prices HD and SD outputs separately. I treat 1080p and 720p as **HD** and 854x480 as **SD** (my reading of
  MediaLive's resolution tiers; check the MediaLive pricing page if the exact boundary matters).
- One viewer, watching the top rendition (about 5.1 Mbps, or 2.3 GB per hour).
- MediaPackage ingest is the sum of all renditions (about 9.9 Mbps, or 4.5 GB per hour).

## What runs while the demo is live (per hour)

| Component | Line item from the price list | Unit price (USD) | Quantity | Cost per hour |
|---|---|---|---|---|
| MediaLive input | Single Pipeline HD AVC input, <10 Mbps | 0.1404 / hour | 1 | 0.1404 |
| MediaLive output, 1080p | Single Pipeline HD AVC output, <10 Mbps, <=30 fps, Standard VQ | 0.4212 / hour | 1 | 0.4212 |
| MediaLive output, 720p | same line as 1080p | 0.4212 / hour | 1 | 0.4212 |
| MediaLive output, 480p | Single Pipeline SD AVC output, <10 Mbps, <=30 fps, Standard VQ | 0.2124 / hour | 1 | 0.2124 |
| MediaConnect active flow | Active Flow usage | 0.16 / hour | 1 | 0.1600 |
| MediaConnect input | 20 Mbps input tier (my reading of `Running20mbpsInput`) | 0.09 / hour | 1 | 0.0900 |
| MediaConnect output to MediaLive | 20 Mbps output tier (my reading of `Running20mbpsOutput`) | 0.04 / hour | 1 | 0.0400 |
| MediaPackage ingest | Ingest | 0.03 / GB | 4.5 GB | 0.1350 |
| MediaPackage origination/packaging | Origination/Packaging | 0.05 / GB | 2.3 GB | 0.1150 |
| CloudFront requests | HTTPS requests | 0.01 / 10,000 | about 1,200 | 0.0012 |
| **Total (priced items)** | | | | **about 1.74** |

**Not priced (could not retrieve or not looked up):**
- CloudFront data transfer out (2.3 GB per hour for one viewer). The price list query returned only request prices; CloudFront
  data transfer is tiered by geography, and I did not verify its free tier.
- MediaConnect data transfer to MediaLive inside the region. The list shows a regional transfer line of 0.01 per GB; at
  about 2.9 GB per hour that would add up to about 0.03 per hour if it applies, which would bring the total to about 1.76.
- MediaPackage v2 live packaging: the price list shows the live ingest and origination/packaging lines above (named `EMP`) and a
  separate line only for v2 multiview, so I assumed v2 live uses the same rates. Unverified.

## Session costs (priced items only)

| Session | Estimated cost |
|---|---|
| 15-minute demo | about 0.43 |
| 1-hour demo | about 1.74 |
| Everything I ran while building this project (roughly one hour of full-stack running in total) | about 2, well under the 25 budget |

## What the Standard (two-pipeline) channel class would cost

The Standard class prices are about 1.67x the single-pipeline prices, not 2x:

| MediaLive line | Single pipeline | Standard |
|---|---|---|
| HD AVC input, <10 Mbps | 0.1404 | 0.2340 |
| HD AVC output, <10 Mbps, <=30 fps | 0.4212 | 0.7020 |
| SD AVC output, <10 Mbps, <=30 fps | 0.2124 | 0.3540 |
| **Ladder total (1 input, 2 HD, 1 SD)** | **1.1952** | **1.9920** |

That adds about 0.80 per hour to the total, or roughly 2.53 per hour overall. Standard also needs a second input and destination, which this
demo does not use.

## Costs when nothing is running

| Item | Cost |
|---|---|
| Secrets Manager (SRT passphrase and CDN identifier) | 0.40 per secret per month while they exist; both are deleted by `terraform destroy` |
| S3 state bucket | not priced here; it holds a few kilobytes |
| AWS Budget | not looked up; the budget is a standard cost budget with no actions |

## Guardrails

A $25 monthly budget (alerts at 50% and 80% of actual spend and at 100% of forecast) lives in `bootstrap/` and survives
`terraform destroy`. Budget alerts lag by hours, so the real controls are `livectl stop`, `terraform destroy` and
`livectl check-clean`.

## How to reproduce these numbers

```bash
scripts/price_lookup.py AWSElementalMediaLive "Single Pipeline (HD|SD) AVC (inputs|outputs)"
scripts/price_lookup.py AWSElementalMediaLive "EML2-USE1-(IN-AVC-(HD|SD)-L10|OUT-AVC-(HD|SD)-L10-30-S)($|,| )"
scripts/price_lookup.py AWSMediaConnect "Usage-Hours"
scripts/price_lookup.py AWSElementalMediaPackage "."
scripts/price_lookup.py AWSSecretsManager "per secret"
aws pricing get-products --region us-east-1 --service-code AmazonCloudFront \
  --filters Type=TERM_MATCH,Field=location,Value="United States"
```

Note: MediaConnect's service code is `AWSMediaConnect` (not `AWSElementalMediaConnect`), and CloudFront entries are
global, so they are found by `location`, not by the `regionCode` filter the script uses.
