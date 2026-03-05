# Clarinet Refactoring Plan - DRY, KISS, YAGNI

## Overview
This document provides a systematic refactoring plan with specific AI assistant prompts to eliminate code duplication, simplify complex logic, and remove unnecessary abstractions in the Clarinet codebase.

## Phase 1: DRY (Don't Repeat Yourself) Refactoring

### 1.1 Create Database Helper Utilities

**Prompt for AI Assistant:**
```
Create a new file src/utils/db_helpers.py with the following helper functions:

1. get_or_404(session, model, id, context=None) - Generic function to get entity or raise NOT_FOUND
2. save_entity(session, entity) - Handle add/commit/refresh pattern
3. bulk_save_entities(session, entities) - Save multiple entities
4. exists_or_404(session, model, id) - Check existence without fetching full entity
5. get_by_field_or_404(session, model, field_name, field_value, context=None) - Get by any field

Make these functions async and properly typed. Import NOT_FOUND from src.exceptions.http.
Add proper docstrings following Google style.
```

### 1.2 Refactor Entity Lookup Pattern

**Prompt for AI Assistant:**
```
Refactor all entity lookup patterns in the following files to use the new db_helpers:

Files to refactor:
- src/api/routers/task.py
- src/api/routers/study.py
- src/api/routers/patient.py
- src/repositories/study_repository.py
- src/repositories/patient_repository.py
- src/repositories/series_repository.py

Replace all patterns like:
entity = await session.get(Model, id)
if entity is None:
    raise NOT_FOUND.with_context(...)

With:
from src.utils.db_helpers import get_or_404
entity = await get_or_404(session, Model, id, context="...")

Ensure all tests still pass after refactoring.
```

### 1.3 Extract Password Hashing Logic

**Prompt for AI Assistant:**
```
In src/services/user_service.py:

1. Create a private method _handle_password_field(user_data: dict) -> dict
2. Move the duplicated password hashing logic from create_user() and update_user() into this method
3. Call this method from both create_user() and update_user()
4. Ensure the logic handles both 'password' and 'hashed_password' fields correctly
5. Add unit tests for the new method
```

### 1.4 Simplify Repository Factory Pattern

**Prompt for AI Assistant:**
```
Refactor src/api/dependencies.py:

1. Create a generic factory function:
   def create_repository_factory(repo_class: Type[T]) -> Callable:
       async def factory(session: SessionDep) -> T:
           return repo_class(session)
       return factory

2. Replace all individual repository factory functions with:
   get_user_repository = create_repository_factory(UserRepository)
   get_study_repository = create_repository_factory(StudyRepository)
   get_patient_repository = create_repository_factory(PatientRepository)
   # etc...

3. Update all imports and usages throughout the codebase
4. Verify dependency injection still works correctly
```

### 1.5 Create Common Repository Mixins

**Prompt for AI Assistant:**
```
Create src/repositories/mixins.py with common repository patterns:

1. RelationsMixin - for get_with_relations() methods
2. BulkOperationsMixin - for bulk create/update/delete
3. SearchMixin - for common search patterns
4. PaginationMixin - for paginated queries

Refactor existing repositories to use these mixins instead of duplicating code.
Focus on UserRepository, StudyRepository, and PatientRepository first.
```

## Phase 2: KISS (Keep It Simple, Stupid) Refactoring

### 2.1 Simplify Complex Task Finding Logic

**Prompt for AI Assistant:**
```
Refactor the find_tasks() function in src/api/routers/task.py:

1. Break down the 113-line function into smaller functions:
   - _build_base_query(session, filters)
   - _apply_status_filter(query, status)
   - _apply_design_filter(query, design_id)
   - _apply_study_filter(query, study_id)
   - _apply_patient_filter(query, patient_id)
   - _apply_date_filters(query, date_from, date_to)
   - _apply_sorting(query, sort_by, sort_order)

2. Use a builder pattern or pipeline approach
3. Each function should be under 20 lines
4. Add type hints and docstrings
5. Create unit tests for each extracted function
```

### 2.2 Simplify Anonymous Name Generation

**Prompt for AI Assistant:**
```
Refactor _generate_anonymous_name() in src/services/study_service.py:

1. Extract the name generation logic into separate functions:
   - _get_base_anonymous_name(index: int) -> str
   - _ensure_unique_name(session, base_name: str, model: Type) -> str

2. Use a simpler approach with string formatting
3. Remove nested conditionals
4. Add comprehensive tests for edge cases
```

### 2.3 Streamline Exception Handling

**Prompt for AI Assistant:**
```
Simplify the exception handling pattern:

1. Create src/utils/exceptions.py with a single exception converter:
   def handle_domain_exception(func):
       # Decorator to automatically convert domain exceptions to HTTP

2. Remove unnecessary domain-to-HTTP exception conversions
3. Use the decorator on service methods that need it
4. Simplify the exception hierarchy - merge similar exceptions
```

### 2.4 Simplify Dependency Injection

**Prompt for AI Assistant:**
```
Refactor complex dependency injection in src/api/dependencies.py:

1. Remove unnecessary type aliases that don't add clarity
2. Simplify nested dependencies where possible
3. Use FastAPI's built-in Depends() more effectively
4. Create a single get_services() function that returns commonly used services as a named tuple
```

## Phase 3: YAGNI (You Aren't Gonna Need It) Refactoring

### 3.1 Remove Unused BaseRepository Methods

**Prompt for AI Assistant:**
```
Analyze and remove unused methods from src/repositories/base.py:

1. Check usage of these methods across the codebase:
   - build_query()
   - execute_query()
   - get_or_create()
   - bulk_create()
   - bulk_update()

2. Remove methods with zero usage
3. Mark rarely used methods as deprecated with comments
4. Consider moving specialized methods to specific repositories that need them
5. Update tests accordingly
```

### 3.2 Simplify Type Aliases

**Prompt for AI Assistant:**
```
Clean up src/types.py and src/api/dependencies.py:

1. Remove redundant type aliases that just rename existing types
2. Keep only aliases that provide meaningful abstraction:
   - JSONDict
   - TaskResult
   - Complex domain-specific types

3. Remove these if unused:
   - Simple repository type aliases
   - Service type aliases that don't add value
   - Redundant form/validation types

4. Update all imports throughout the codebase
```

### 3.3 Remove Over-Engineered Abstractions

**Prompt for AI Assistant:**
```
Identify and remove unnecessary abstractions:

1. Search for abstract base classes with single implementations
2. Remove intermediate layers that just pass through calls
3. Simplify the repository pattern where it's overkill for simple CRUD
4. Consider using direct SQLAlchemy queries for simple cases
5. Document why remaining abstractions are necessary
```

### 3.4 Clean Up Configuration

**Prompt for AI Assistant:**
```
Audit src/settings.py and configuration files:

1. Identify unused configuration options
2. Remove settings that are never referenced in code
3. Consolidate similar settings
4. Add comments explaining non-obvious settings
5. Create settings.toml.minimal with only essential settings
```

## Phase 4: Testing and Validation

### 4.1 Create Integration Tests

**Prompt for AI Assistant:**
```
Create comprehensive integration tests for refactored code:

1. Test the new db_helpers functions with various models
2. Test repository methods after mixin refactoring
3. Test simplified task finding logic with various filters
4. Verify exception handling works correctly
5. Ensure all API endpoints still function properly

Create these test files:
- tests/utils/test_db_helpers.py
- tests/repositories/test_mixins.py
- tests/api/test_refactored_endpoints.py
```

### 4.2 Performance Testing

**Prompt for AI Assistant:**
```
Create performance benchmarks to ensure refactoring didn't degrade performance:

1. Create tests/performance/benchmark_queries.py
2. Measure query performance before and after refactoring
3. Test bulk operations performance
4. Check memory usage for large datasets
5. Document any performance improvements or regressions
```

### 4.3 Code Quality Validation

**Prompt for AI Assistant:**
```
Run code quality checks and fix any issues:

1. Run: ruff format src/ tests/
2. Run: ruff check src/ tests/ --fix
3. Run: mypy src/ --strict
4. Check test coverage: pytest --cov=src tests/
5. Ensure coverage is above 80% for refactored code
6. Update documentation for changed APIs
```

## Phase 5: Documentation Updates

### 5.1 Update CLAUDE.md

**Prompt for AI Assistant:**
```
Update CLAUDE.md with new patterns and helpers:

1. Document the new db_helpers utilities and when to use them
2. Add examples of using repository mixins
3. Update anti-patterns section with patterns we've eliminated
4. Add best practices discovered during refactoring
5. Include performance tips based on benchmarks
```

### 5.2 Create Migration Guide

**Prompt for AI Assistant:**
```
Create REFACTORING_MIGRATION.md with:

1. List of all breaking changes
2. Migration steps for existing code
3. Mapping of old patterns to new patterns
4. Common pitfalls and how to avoid them
5. Rollback procedures if needed
```

## Execution Order

1. **Week 1**: Phase 1 (DRY) - Focus on eliminating duplication
2. **Week 2**: Phase 2 (KISS) - Simplify complex logic
3. **Week 3**: Phase 3 (YAGNI) - Remove unnecessary code
4. **Week 4**: Phase 4-5 - Testing, validation, and documentation

## Success Metrics

- **Code Reduction**: Aim for 20-30% reduction in lines of code
- **Complexity**: Reduce cyclomatic complexity by 40%
- **Test Coverage**: Maintain or improve current coverage
- **Performance**: No degradation in response times
- **Duplication**: Reduce code duplication index by 50%

## Rollback Plan

If refactoring causes issues:

1. Each refactoring should be in a separate commit
2. Create feature branches for each phase
3. Run full test suite before merging
4. Keep performance benchmarks for comparison
5. Document all changes in commit messages

## Notes for AI Assistant

When executing these prompts:
- Always run tests after each refactoring step
- Preserve existing functionality unless explicitly changing it
- Follow the project's code style guide in CLAUDE.md
- Create meaningful commit messages
- Ask for clarification if requirements are ambiguous
- Suggest improvements if you identify additional issues

## Completion Checklist

- [ ] All DRY violations addressed
- [ ] Complex functions simplified
- [ ] Unused code removed
- [ ] Tests updated and passing
- [ ] Documentation updated
- [ ] Performance benchmarks completed
- [ ] Code quality checks passing
- [ ] Migration guide created