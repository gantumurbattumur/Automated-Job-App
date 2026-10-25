// Workday applications are a multi-step wizard inside your signed-in session: fill whatever step is on screen.
var JP = window.JP || (window.JP = {});
JP.workday = {
  is: () => /myworkday(jobs|site)?\.com$/.test(location.hostname),
  step: () => (document.querySelector('[data-automation-id="progressBarActiveStep"]')?.innerText || '').replace(/\s+/g, ' ').trim(),
  isResumeInput: f => /file-upload/.test(f.el.dataset.automationId || '') || /resume|cv\b/i.test(f.label),
  signInScreen: () => !!document.querySelector('[data-automation-id="signInContent"], [data-automation-id="createAccountLink"]'),
};
