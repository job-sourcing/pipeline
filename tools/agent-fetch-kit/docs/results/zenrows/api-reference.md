> ## Documentation Index
> Fetch the complete documentation index at: https://docs.zenrows.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Introduction to Fetch

> Explore the full Zenrows Fetch reference, including endpoint parameters, authentication, pricing tiers, and feature documentation.

Zenrows Fetch (formerly, Universal Scraper API) is a versatile tool designed to simplify and enhance the process of extracting data from websites. Whether you're dealing with static or dynamic content, our API provides a range of features to meet your scraping needs efficiently.

With Premium Proxies, Zenrows gives you access to over 55 million residential IPs from 190+ countries, ensuring 99.9% uptime and highly reliable scraping sessions. Our system also handles advanced fingerprinting, header rotation, and IP management, **enabling you to scrape even the most protected sites without needing to manually configure these elements**.

Zenrows makes it easy to bypass complex anti-bot measures, handle JavaScript-heavy sites, and interact with web elements dynamically (all with the right features enabled).

## Key Features

### Adaptive Stealth Mode

Let Zenrows automatically choose the proper configuration for each request. Set `mode=auto`, and the API starts with the cheapest viable setup, then escalates to JavaScript rendering or Premium Proxies only when needed. So you get reliable extraction without manual tuning or overpaying.

**When to use:** Use Adaptive Stealth Mode when scraping multiple or unknown sites, when you want to minimize maintenance as sites change, or when reliability matters more than fine-grained control over parameters.

**Real-world scenarios:**

* Production workloads across many different domains
* Sites whose anti-bot or rendering requirements change over time
* Ensuring successful extraction by starting with simple requests and automatically escalating to JS rendering or premium proxies only when needed

**Additional options:**

* You are billed only for the configuration that succeeds; failed attempts are not charged
* Managed automatically: `js_render` and `premium_proxy` (you are also allowed to choose the `proxy_country`)
* Other parameters (e.g. `js_instructions`, `custom_headers`) work alongside Adaptive Stealth Mode

### JavaScript Rendering

Render JavaScript on web pages using a headless browser to scrape dynamic content that traditional methods might miss.

**When to use:** Use this feature when targeting modern websites built with JavaScript frameworks (React, Vue, Angular), single-page applications (SPAs), or any site that loads content dynamically after the initial page load.

**Real-world scenarios:**

* E-commerce product listings that load items as you scroll
* Dashboards and analytics platforms that render charts/data with JavaScript
* Social media feeds that dynamically append content
* Sites that hide certain content until JavaScript is rendered

**Additional options:**

* Wait times to ensure elements are fully loaded
* Interaction with the page to click buttons, fill forms, or scroll
* Screenshot capabilities for visual verification
* CSS-based extraction of specific elements

### Premium Proxies

Leverage a vast network of residential IP addresses across 190+ countries, ensuring a 99.9% uptime for uninterrupted scraping.

**When to use:** Essential for accessing websites with sophisticated anti-bot systems, geo-restricted content, or when you consistently encounter blocks with standard datacenter IPs.

**Real-world scenarios:**

* Scraping major e-commerce platforms (Amazon, Walmart)
* Accessing real estate listings (Zillow, Redfin)
* Gathering pricing data from travel sites (Expedia, Booking.com)
* Collecting data from financial or investment platforms

**Additional options:**

* Geolocation selection to access region-specific content
* Automatic IP rotation to prevent detection

### Custom Headers

Add custom HTTP headers to your requests for more control over how your requests appear to target websites.

**When to use:** When you need to mimic specific browser behavior, set cookies, or a referer.

**Real-world scenarios:**

* Setting language preferences to get content in specific languages
* Adding a referer to avoid being blocked by bot detection systems

### Session Management

Use a session ID to maintain the same IP address across multiple requests for up to 10 minutes.

**When to use:** When scraping multi-page flows or processes that require maintaining the same IP across multiple requests.

**Real-world scenarios:**

* Multi-step forms processes
* Maintaining consistent session for search results and item visits

### Advanced Data Extraction

Extract only the data you need with CSS selectors or automatic parsing.

**When to use:** When you need specific information from pages and want to reduce bandwidth usage or simplify post-processing.

**Real-world scenarios:**

* Extracting pricing information from product pages
* Gathering contact details from business directories
* Collecting specific metrics from analytics pages

### Language agnostic

While Python examples are provided, the API works with any programming language that can make HTTP requests.

## Parameter Overview

Customize your scraping requests using the following parameters:

| PARAMETER                                                                      | TYPE            | DEFAULT                                                              | DESCRIPTION                                                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------ | --------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **apikey** `required`                                                          | `string`        | [**Get Your Free API Key**](https://app.zenrows.com/register?p=free) | Your unique API key for authentication                                                                                                                                                                                                                                                                       |
| **url** `required`                                                             | `string`        |                                                                      | The URL of the page you want to scrape                                                                                                                                                                                                                                                                       |
| [**mode**](/fetch/features/adaptive-stealth-mode)                              | `string`        |                                                                      | Enables Adaptive Stealth Mode when set to `auto`.<br />*Use-case: [Adaptive Stealth Mode vs Manual Parameters](/fetch/features/adaptive-stealth-mode#adaptive-stealth-mode-vs-manual-configuration)*                                                                                                         |
| [**js\_render**](/fetch/features/js-rendering)                                 | `boolean`       | false                                                                | Enable JavaScript rendering with a headless browser. Essential for modern web apps, SPAs, and sites with dynamic content.<br />*Use-case: [Load dynamic pages (SPAs, dashboards, infinite scroll)](/fetch/features/js-rendering#when-to-use-javascript-rendering)*                                           |
| [**js\_instructions**](/fetch/features/js-instructions)                        | `string`        |                                                                      | Execute custom JavaScript on the page to interact with elements, scroll, click buttons, or manipulate content. Use when you need to perform actions before the content is returned.<br />*Use-case: [Submit forms or simulate user actions](/fetch/features/js-instructions#common-use-cases-and-workflows)* |
| [**custom\_headers**](/fetch/features/headers)                                 | `boolean`       | false                                                                | Enables you to add custom HTTP headers to your request, such as cookies or referer, to better simulate real browser traffic or provide site-specific information.<br />*Use-case: [Simulate browser behavior for reliable access](/fetch/features/headers#referrer-simulation)*                              |
| [**premium\_proxy**](/fetch/features/premium-proxy)                            | `boolean`       | false                                                                | Route through residential IPs for protected access to sites with anti-bot protection.<br />*Use-case: [Access protected sites using residential IPs](/fetch/features/premium-proxy#basic-usage)*                                                                                                             |
| [**proxy\_country**](/fetch/features/proxy-country)                            | `string`        |                                                                      | Set the country of the IP used for the request (requires Premium Proxies). Use for accessing geo-restricted content or seeing region-specific content.<br />*Use-case: [Access geo-restricted content](/fetch/features/proxy-country#common-use-cases)*                                                      |
| [**session\_id**](/fetch/features/other#session-id)                            | `integer`       |                                                                      | Maintain the same IP for multiple requests for up to 10 minutes. Essential for multi-step processes.<br />*Use-case: [Keep session/IP across requests](/fetch/features/other#session-id)*                                                                                                                    |
| [**original\_status**](/fetch/features/other#original-http-code)               | `boolean`       | false                                                                | Return the original HTTP status code from the target page. Useful for debugging in case of errors.<br />*Use-case: [Debug failed requests](/fetch/features/other#original-http-code)*                                                                                                                        |
| [**allowed\_status\_codes**](/fetch/features/other#return-content-on-error)    | `string`        |                                                                      | Returns the content even if the target page fails with specified status codes. Useful for debugging or when you need content from error pages.<br />*Use-case: [Debug failed requests and return the failed page](/fetch/features/other#return-content-on-error)*                                            |
| [**wait\_for**](/fetch/features/wait-for)                                      | `string`        |                                                                      | Wait for a specific CSS Selector to appear in the DOM before returning content. Essential for elements that load asynchronously.<br />*Use-case: [Capture elements that load at unpredictable times](/fetch/features/wait-for#basic-usage)*                                                                  |
| [**wait**](/fetch/features/wait)                                               | `integer`       | 0                                                                    | Wait a fixed amount of milliseconds after page load. Use for sites that load content in stages or have delayed rendering.<br />*Use-case: [Pause the request process after the initial page load](/fetch/features/wait#basic-usage)*                                                                         |
| [**block\_resources**](/fetch/features/block-resources)                        | `string`        |                                                                      | Block specific resources (images, fonts, etc.) from loading to speed up scraping and reduce bandwidth usage. Enabled by default, careful when changing it.<br />*Use-case: [Optimize performance and minimize bandwidth](/fetch/features/block-resources#basic-usage)*                                       |
| [**json\_response**](/fetch/features/json-response)                            | `string`        | false                                                                | Capture network requests in JSON format, including XHR or Fetch data. Ideal for intercepting API calls made by the web page.<br />*Use-case: [Capture API calls and background requests](/fetch/features/json-response#basic-usage)*                                                                         |
| [**css\_extractor**](/fetch/features/css-extractor)                            | `string (JSON)` |                                                                      | Extract specific elements using CSS selectors. Perfect for targeting only the data you need from complex pages.<br />*Use-case: [Extract only specific data fields](/fetch/features/css-extractor)*                                                                                                          |
| [**extract**](/extract/setup)                                                  | `string`        |                                                                      | Extract structured JSON data from the page automatically, with no selectors to write. Beta. Accepted value: `auto`.<br />*Use-case: [Get structured data from a page](/extract/setup)*                                                                                                                       |
| [**autoparse**](/fetch/features/autoparse)                                     | `boolean`       | false                                                                | Automatically extract structured data from HTML. Great for quick extraction without specifying selectors.<br />*Use-case: [Parse data in JSON format automatically](/fetch/features/autoparse)*                                                                                                              |
| [**response\_type**](/fetch/features/markdown)                                 | `string`        |                                                                      | Convert HTML to other formats (Markdown, Plaintext, PDF). Useful for content readability, storage, or to train AI models.<br />*Use-case: [Extract only specific formats](/fetch/features/markdown)*                                                                                                         |
| [**screenshot**](/fetch/features/screenshot)                                   | `boolean`       | false                                                                | Capture an above-the-fold screenshot of the page. Helpful for visual verification or debugging.<br />*Use-case: [Visual verification of page rendering](/fetch/features/screenshot)*                                                                                                                         |
| [**screenshot\_fullpage**](/fetch/features/screenshot)                         | `boolean`       | false                                                                | Capture a full-page screenshot. Useful for content that extends below the fold.<br />*Use-case: [Capture complete page content](/fetch/features/screenshot#capture-mode)*                                                                                                                                    |
| [**screenshot\_selector**](/fetch/features/screenshot)                         | `string`        |                                                                      | Capture a screenshot of a specific element using CSS Selector. Perfect for capturing specific components.<br />*Use-case: [Capture specific page elements](/fetch/features/screenshot#capture-mode)*                                                                                                         |
| [**screenshot\_format**](/fetch/features/screenshot#image-format-and-quality)  | `string`        |                                                                      | Choose between `png` (default) and `jpeg` formats for screenshots.<br />*Use-case: [Choose appropriate image format](/fetch/features/screenshot#image-format-and-quality)*                                                                                                                                   |
| [**screenshot\_quality**](/fetch/features/screenshot#image-format-and-quality) | `integer`       |                                                                      | For JPEG format, set quality from `1` to `100`. Lower values reduce file size but decrease quality.<br />*Use-case: [Control image compression levels](/fetch/features/screenshot#image-format-and-quality)*                                                                                                 |
| [**outputs**](/fetch/features/output-filters)                                  | `string`        |                                                                      | Specify which data types to extract from the scraped HTML.<br />*Use-case: [Extract only specific data fields](/fetch/features/output-filters)*                                                                                                                                                              |

## Pricing

Zenrows uses credit-based pricing. Every plan gives you a monthly credit allowance shared across Fetch, Extract, Batch, and Browser Sessions. The Free plan starts at 5,000 credits/month; the first paid tier, Build B1, is \$19/month for 45,000 credits, enough for 45,000 basic requests a month. Enterprise plans scale beyond 12.5M credits/month.

For complex or highly protected websites, enabling advanced features like JavaScript rendering (`js_render`) and Premium Proxies unlocks Zenrows' full potential, ensuring the best success rate possible.

The credit cost depends on the complexity of the request. You only spend the credits the scraping tech you need actually requires. With Adaptive Stealth Mode (`mode=auto`), the same credit weights apply; you're billed only for the configuration that succeeds. If multiple internal attempts are needed before success, only the successful request is charged.

* **Standard request:** 1 credit
* **JS rendering:** 5 credits
* **Premium proxies:** 10 credits
* **Both (JS & proxies, protected):** 25 credits

<Tip>For the full plan table, credit costs per tier, and top-up pricing, see our [pricing documentation page](/first-steps/pricing).</Tip>

### Concurrency

Concurrency determines how many requests can run simultaneously, and scales by plan tier:

| Tier       | Concurrency Limit      |
| ---------- | ---------------------- |
| Free       | 5                      |
| Build      | 20                     |
| Launch     | 50                     |
| Growth     | 100                    |
| Scale      | 200                    |
| Enterprise | Custom (400 to 1,000+) |

**Important notes about concurrency:**

* Canceling requests on the client side does NOT immediately free up concurrency slots
* The server continues processing canceled requests until completion
* If you exceed your concurrency limit, you'll receive a `429 Too Many Requests` error

**If response size is exceeded:**

* You'll receive a `413 Content Too Large` error
* No partial data will be returned when a size limit is hit

**Strategies for handling large pages:**

1. **Use CSS selectors**: Target only the specific data you need with `css_extractor` parameter
2. **Use `response_type`**: Convert to markdown or plaintext to reduce size
3. **Disable screenshots**: If using `screenshot` features, these can significantly increase response size
4. **Segment your scraping**: Break down large pages into smaller, more manageable sections

## Response Headers

Zenrows provides useful information through response headers:

| Header                    | Description                                                   | Example Value                      | Usage                           |
| ------------------------- | ------------------------------------------------------------- | ---------------------------------- | ------------------------------- |
| **Concurrency-Limit**     | Maximum concurrent requests allowed by your plan              | `20`                               | Monitor your plan's capacity    |
| **Concurrency-Remaining** | Available concurrent request slots                            | `17`                               | Adjust request rate dynamically |
| **X-Request-Cost**        | Cost of this request                                          | `0.001`                            | Track balance consumption       |
| **X-Request-Id**          | Unique identifier for this request                            | `67fa4e35647515d8ad61bb3ee041e1bb` | Include when contacting support |
| **Zr-Final-Url**          | The final URL after any redirects occurred during the request | `https://example.com/page?id=123`  | Track redirects                 |

Why these headers matter:

* **Monitoring usage**: Track your concurrent usage and stay within limits
* **Support requests**: When reporting issues, always include the `X-Request-Id` for faster troubleshooting
* **Cost tracking**: The `X-Request-Cost` helps you monitor your usage per request
* **Redirection tracking**: `Zr-Final-Url` shows where you ended up after any redirects

## Additional Considerations

Beyond the core features and limits, these additional aspects are important to consider when using Fetch:

### Cancelled Request Behavior

When you cancel a request on the client side:

* The server **continues processing** the request until completion
* The concurrency slot remains occupied for up to 3 minutes
* This can result in unexpected `429 Too Many Requests` errors
* Implement request timeouts carefully to avoid depleting concurrency slots

### Security Best Practices

To keep your Zenrows integration secure:

* Store API keys as environment variables, never hardcode them
* Monitor usage patterns to detect unauthorized use
* Rotate API keys periodically for critical applications

### Regional Performance Optimization

To optimize performance based on target website location:

* Consider the geographical distance between your servers and the target website
* For global applications, distribute scraping across multiple regions - the system does it by default
* Monitor response times by region to identify optimization opportunities
* For region-specific content, use the appropriate `proxy_country` parameter

### Compression Support

Zenrows API supports response compression to optimize bandwidth usage and improve performance. Enabling compression offers several benefits for your scraping operations:

* **Reduced latency**: Smaller response sizes mean faster data transfer times
* **Lower bandwidth consumption**: Minimize data transfer costs and usage
* **Improved client performance**: Less data to process means reduced memory usage

Zenrows supports the following compression encodings: `gzip`, `deflate`, `br`.

To use compression, include the appropriate `Accept-Encoding` header in your requests. Most HTTP clients already compress the request automatically. But you can also provide simple options to enable it:

<CodeGroup>
  ```python Python (Requests) theme={"dark"}
  import requests

  response = requests.get(
      "https://api.zenrows.com/v1/",
      params={
          "apikey": "YOUR_API_KEY",
          "url": "https://example.com"
      },
      # Python Requests uses compression by default
      # headers={"Accept-Encoding": "gzip,  deflate"}
  )
  ```

  ```javascript Javascript (Axios) theme={"dark"}
  const axios = require('axios');

  const response = await axios.get('https://api.zenrows.com/v1/', {
    params: {
      apikey: 'YOUR_API_KEY',
      url: 'https://example.com'
    },
    headers: {
      'Accept-Encoding': 'gzip, deflate', // axios does not support br by default
    },
    decompress: true, // Enables automatic handling of compression
  });
  ```

  ```bash cURL theme={"dark"}
  curl --compressed "https://api.zenrows.com/v1/?apikey=YOUR_API_KEY&url=https://example.com"
  ```
</CodeGroup>

<Tip>Most modern HTTP clients automatically handle decompression, so you'll receive the uncompressed content in your response object without any additional configuration.</Tip>
