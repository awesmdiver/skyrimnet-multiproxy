# v2.2.0 — More AI Choices: GLM & Nano-GPT Support

## What's New

**Two new ways to power your NPC dialogue!**

Looking for dialogue options outside of Claude, or want characters with fewer conversational boundaries? You can now connect two additional services using your own account keys:

* **GLM (z.ai):** Built for quick responses. The proxy automatically tunes settings under the hood to ensure NPCs reply quickly without unnecessary delays.
* **Nano-GPT:** Gives you access to a huge catalog of community models, including great uncensored and roleplay-focused options for deeper immersion.

**Easier Dashboard Setup**

When you open your local dashboard (`http://127.0.0.1:8000`), you will see dedicated cards for both GLM and Nano-GPT right alongside OpenRouter. Just paste in your key, click **Save**, and pick a ready-to-use model from the test dropdown to make sure everything works.

**Visible Usage Stats**

The dashboard test now displays response sizes alongside reply speeds, so you can see exactly how much dialogue was sent and received at a glance.

## Improvements & Polish

* **Helpful Links:** The OpenRouter card now includes a direct link to sign up and grab a key, just like the other providers.
* **Safer Logs:** If you have file logging turned on for troubleshooting, your GLM keys are now hidden and protected in log files alongside your other keys.
* **Easy Upgrading:** If you already have things running smoothly, you don't need to rebuild anything. Just paste your new keys into the dashboard whenever you want to try out the new providers.

## Good to Know

* **Safe to Use:** Both GLM and Nano-GPT connect through their standard, official services, making them a safe and fully supported way to run dialogue.
* **No Game Plugin Changes:** The main plugin file (`ProxyLauncher.dll`) is unchanged. This update only affects the background proxy helper.

## Special Thanks

A huge thank you to **cleanestpoison** for suggesting the idea and creating the initial prototype that brought GLM and Nano-GPT into the project!

## Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/claude-skyrimnet-proxy-launcher/issues)!
