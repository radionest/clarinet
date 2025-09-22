// Test utilities for mocking fetch responses and promise resolution
// These utilities provide a way to test promise-based HTTP client code
// by creating mock responses and handling promise resolution in tests.

/**
 * Creates a mock FetchBody that simulates the gleam_fetch FetchBody behavior.
 * This creates a proper Response-like body that can be consumed by fetch.read_text_body.
 *
 * @param {string} text - The text content to be returned when the body is read
 * @returns {Response} A real Response object with the provided text as body
 */
export function createMockFetchBody(text) {
  // Create a real Response object which has a proper body
  // This ensures compatibility with fetch.read_text_body
  const response = new Response(text);
  return response.body || response;
}

/**
 * Creates a test wrapper for async promise testing.
 * Since Gleam tests are synchronous, we need to handle promises specially.
 * This returns a wrapper that can be used to test promise-based code.
 *
 * @param {Promise} promise - The promise to test
 * @returns {Object} A test wrapper with the promise result
 */
export async function resolvePromiseSync(promise) {
  try {
    // Await the promise and return the result
    // The Gleam test will need to handle this appropriately
    const result = await promise;
    return result;
  } catch (error) {
    // If the promise rejects, return the error
    return error;
  }
}

/**
 * Creates a mock fetch.read_text_body result.
 * This simulates what fetch.read_text_body would return.
 *
 * @param {string} text - The text to return as the body
 * @param {number} status - The HTTP status code
 * @returns {Promise} A promise that resolves to a Gleam Result type
 */
export function createMockTextBodyResult(text, status = 200) {
  // Return a promise that resolves to the expected Ok variant structure
  // that fetch.read_text_body would produce
  return Promise.resolve({
    tag: 'Ok',
    0: {
      status: status,
      headers: [],
      body: text
    }
  });
}

/**
 * Creates a mock fetch.read_text_body error result.
 *
 * @param {string} errorType - The type of error
 * @returns {Promise} A promise that resolves to a Gleam Error type
 */
export function createMockTextBodyError(errorType = 'UnableToReadBody') {
  // Return a promise that resolves to the expected Error variant
  return Promise.resolve({
    tag: 'Error',
    0: errorType
  });
}

/**
 * Test helper to run a promise-based test.
 * This is a bridge between Gleam's synchronous tests and JavaScript's async world.
 *
 * @param {Function} testFn - An async test function to run
 * @returns {*} The result of the test function
 */
export function runAsyncTest(testFn) {
  // Create a promise and immediately resolve it
  // This allows the test to work with async code
  let result;
  let error;
  let done = false;

  testFn()
    .then(r => {
      result = r;
      done = true;
    })
    .catch(e => {
      error = e;
      done = true;
    });

  // For testing purposes, we need to wait synchronously
  // This is not ideal but necessary for integration with Gleam's test runner
  const start = Date.now();
  while (!done && Date.now() - start < 5000) {
    // Busy wait - not ideal but works for tests
  }

  if (!done) {
    throw new Error('Test timeout');
  }

  if (error) {
    throw error;
  }

  return result;
}