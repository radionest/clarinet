// JavaScript FFI for HTTP requests

export async function fetchRequest(request) {
  try {
    const { method, host, path, headers, body } = request;

    // Build full URL
    const protocol = window.location.protocol;
    const url = `${protocol}//${host}${path}`;

    // Build fetch options
    const options = {
      method: method.toUpperCase(),
      headers: headers.reduce((acc, [key, value]) => {
        acc[key] = value;
        return acc;
      }, {}),
    };

    // Add body if present
    if (body && method !== 'GET' && method !== 'DELETE') {
      options.body = body;
    }

    // Make the request
    const response = await fetch(url, options);

    // Parse response
    const text = await response.text();
    let data;

    try {
      data = JSON.parse(text);
    } catch {
      // If not JSON, wrap in object
      data = { message: text };
    }

    // Return result based on status
    if (response.ok) {
      return { Ok: data };
    } else {
      return {
        Error: {
          constructor: "ServerError",
          code: response.status,
          message: response.statusText || "Request failed"
        }
      };
    }
  } catch (error) {
    return {
      Error: {
        constructor: "NetworkError",
        0: error.message || "Network error"
      }
    };
  }
}

// Helper function to store token in localStorage
export function storeToken(token) {
  if (typeof window !== 'undefined' && window.localStorage) {
    window.localStorage.setItem('clarinet_token', token);
  }
}

// Helper function to retrieve token from localStorage
export function getStoredToken() {
  if (typeof window !== 'undefined' && window.localStorage) {
    return window.localStorage.getItem('clarinet_token') || null;
  }
  return null;
}

// Helper function to clear token from localStorage
export function clearToken() {
  if (typeof window !== 'undefined' && window.localStorage) {
    window.localStorage.removeItem('clarinet_token');
  }
}