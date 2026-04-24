dotnet new sln -o Client
cd Client

# 3. Create the Avalonia UI application
dotnet new avalonia.app -o Client.UI

# 4. Create the Unit Test project (using xUnit, the standard for modern .NET)
dotnet new xunit -o Client.Tests

# 5. Add both projects to the Solution
dotnet sln add Client.UI/Client.UI.csproj
dotnet sln add Client.Tests/Client.Tests.csproj

# 6. Add a reference so the Test project can access the UI project's code
dotnet add Client.Tests/Client.Tests.csproj reference Client.UI/Client.UI.csproj
