# Clarinet Frontend

A modern single-page application (SPA) frontend for the Clarinet medical imaging framework, built with Gleam and Lustre.

## Overview

The frontend provides a web-based user interface for:
- Managing medical imaging studies
- Creating and executing analysis tasks
- User management and authentication
- Dynamic form generation for task results

## Technology Stack

- **Gleam**: Functional programming language with excellent type safety
- **Lustre**: Elm-inspired web framework for Gleam
- **Modem**: Client-side routing
- **MVU Architecture**: Model-View-Update pattern for predictable state management

## Project Structure

```
src/frontend/
├── src/
│   ├── api/              # API client and models
│   │   ├── client.gleam  # HTTP client
│   │   ├── auth.gleam    # Authentication endpoints
│   │   ├── models.gleam  # Static type definitions
│   │   └── types.gleam   # Common types
│   ├── components/       # Reusable UI components
│   │   ├── layout.gleam  # Main layout with navbar
│   │   └── forms/        # Form components
│   ├── pages/           # Application pages
│   │   ├── home.gleam   # Dashboard
│   │   ├── login.gleam  # Authentication
│   │   ├── studies/     # Study management
│   │   ├── tasks/       # Task management
│   │   └── users/       # User management
│   ├── ffi/             # JavaScript interop
│   ├── router.gleam     # Client-side routing
│   ├── store.gleam      # Global state management
│   ├── main.gleam       # Application entry
│   └── clarinet.gleam   # Build entry point
├── static/              # Static assets
│   ├── index.html       # SPA entry
│   ├── base.css        # Base styles
│   └── 404.html        # Error page
├── scripts/            # Build scripts
│   ├── build.sh        # Production build
│   └── watch.sh        # Development watch
└── gleam.toml          # Gleam configuration
```

## Getting Started

### Prerequisites

- Gleam compiler (>= 1.4.0)
- Erlang/OTP (>= 26)
- Node.js (>= 18) for JavaScript runtime

### Installation

1. Install Gleam and dependencies:
```bash
# Using the CLI
python -m clarinet frontend install

# Or manually
cd src/frontend
gleam deps download
```

2. Build the frontend:
```bash
# Using the CLI
python -m clarinet frontend build

# Or manually
cd src/frontend
./scripts/build.sh
```

### Development

1. Enable frontend in your `settings.toml`:
```toml
frontend_enabled = true
frontend_dev_mode = true
```

2. Start the development server:
```bash
# Start backend with frontend enabled
python -m clarinet run

# In another terminal, watch for frontend changes
cd src/frontend
./scripts/watch.sh
```

3. Access the application at http://localhost:8000

## Key Features

### Type-Safe API Client

The API client provides type-safe communication with the backend:

```gleam
import api/client
import api/auth

// Create client and authenticate
let config = client.create_client("http://localhost:8000")
auth.login(config, "username", "password")
```

### Static Typed Forms

Core models (Patient, Study, TaskDesign, User) use fully typed forms:

```gleam
import components/forms/study_form

// Type-safe form with compile-time validation
study_form.view(form_data, errors)
```

### Dynamic Forms (Coming Soon)

Task results will support dynamic forms generated from JSON Schema using Formosh.

### MVU Architecture

The application follows the Model-View-Update pattern:

```gleam
// Model: Application state
type Model {
  Model(
    route: Route,
    user: Option(User),
    studies: List(Study),
    ...
  )
}

// Messages: All possible state changes
type Msg {
  Navigate(Route)
  LoginSuccess(token: String, user: User)
  LoadStudies
  ...
}

// Update: Pure state transformation
fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg)) {
  case msg {
    Navigate(route) -> #(set_route(model, route), navigate_effect(route))
    ...
  }
}
```

## Customization

### CSS Variables

Customize the appearance by overriding CSS variables in `/static/custom/custom.css`:

```css
:root {
  --primary-color: #your-color;
  --navbar-height: 70px;
  --font-family: 'Your Font';
}
```

### Adding Pages

1. Create a new page module in `src/pages/`
2. Add route in `src/router.gleam`
3. Handle route in `src/main.gleam`
4. Add navigation link in `src/components/layout.gleam`

## API Integration

The frontend communicates with the FastAPI backend through the `/api` prefix:

- `/api/auth/*` - Authentication
- `/api/studies/*` - Study management
- `/api/tasks/*` - Task operations
- `/api/users/*` - User management

## Building for Production

```bash
# Build optimized JavaScript
python -m clarinet frontend build

# The output will be in:
# build/dev/javascript/clarinet.mjs
```

## Testing

Run tests with:
```bash
cd src/frontend
gleam test
```

## Troubleshooting

### Build Errors

If you encounter import errors, ensure all dependencies are downloaded:
```bash
gleam deps download
gleam clean
gleam build
```

### Frontend Not Loading

1. Check that `frontend_enabled = true` in settings
2. Verify build output exists in `build/dev/javascript/`
3. Check browser console for JavaScript errors
4. Ensure the backend is running and accessible

### Authentication Issues

- Tokens are stored in localStorage
- Clear browser storage if experiencing auth loops
- Check CORS settings if API calls fail

## Future Enhancements

- [ ] Formosh integration for dynamic forms
- [ ] Real-time updates with WebSockets
- [ ] Advanced filtering and search
- [ ] Data visualization components
- [ ] Offline support with service workers
- [ ] Comprehensive test coverage
- [ ] Performance optimizations

## Contributing

When contributing to the frontend:

1. Follow Gleam conventions and formatting
2. Maintain type safety - avoid `panic` and handle all errors
3. Write tests for new functionality
4. Update this README for significant changes
5. Use meaningful commit messages

## License

Part of the Clarinet medical imaging framework.