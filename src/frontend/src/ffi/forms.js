// Form-related JavaScript FFI functions

// Get input value by element ID
export function getInputValue(id) {
  const element = document.getElementById(id);
  return element ? element.value : '';
}

// Set input value by element ID
export function setInputValue(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.value = value;
  }
}

// Get all form values as an object
export function getFormValues(formId) {
  const form = document.getElementById(formId);
  if (!form) return {};

  const formData = new FormData(form);
  const values = {};

  for (const [key, value] of formData.entries()) {
    values[key] = value;
  }

  return values;
}

// Set multiple form values
export function setFormValues(formId, values) {
  const form = document.getElementById(formId);
  if (!form) return;

  Object.keys(values).forEach(key => {
    const element = form.elements[key];
    if (element) {
      element.value = values[key];
    }
  });
}

// Validate email format
export function isValidEmail(email) {
  const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return re.test(email);
}

// Clear form
export function clearForm(formId) {
  const form = document.getElementById(formId);
  if (form) {
    form.reset();
  }
}

// Focus first invalid field
export function focusFirstInvalid(formId) {
  const form = document.getElementById(formId);
  if (!form) return;

  const firstInvalid = form.querySelector(':invalid');
  if (firstInvalid) {
    firstInvalid.focus();
  }
}

// Add setTimeout function for effects
export function setTimeout(delay, callback) {
  window.setTimeout(callback, delay);
}