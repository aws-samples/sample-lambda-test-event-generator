# C# Lambda Setup Guide

## Quick Setup for Test Generation

The test generator requires **source code** (`.cs` files) to analyze your Lambda function.

## Why Source Code?

- ✅ Better test generation with comments and structure
- ✅ Works across all languages consistently
- ✅ Industry standard for test generation tools

## .NET Project Setup (Recommended)

Add to your `.csproj`:

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <GenerateRuntimeConfigurationFiles>true</GenerateRuntimeConfigurationFiles>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="Amazon.Lambda.Core" Version="2.2.0" />
    <PackageReference Include="Amazon.Lambda.Serialization.SystemTextJson" Version="2.4.0" />
  </ItemGroup>

  <!-- Include source files for test generation -->
  <ItemGroup>
    <Content Include="**/*.cs">
      <CopyToOutputDirectory>PreserveNewest</CopyToOutputDirectory>
    </Content>
  </ItemGroup>
</Project>
```

Build and deploy:
```bash
dotnet publish -c Release
cd bin/Release/net8.0/publish
cp ../../../../Function.cs .
zip -r ../../../../function.zip .
```

## Manual ZIP (Simple Projects)

```bash
dotnet build -c Release
cd bin/Release/net8.0
cp ../../../Function.cs .
zip -r ../../../function.zip . -i "*.dll" "*.cs" "*.json"
```

## Verification

```bash
unzip -l function.zip | grep .cs
# Should show: Function.cs
```

## Works with SAM/CloudFormation/Terraform

Once you configure your `.csproj` with the `<Content Include="**/*.cs">` section, source files are **automatically included** in every build, regardless of deployment method:

- ✅ AWS CLI
- ✅ Lambda Console upload
- ✅ SAM CLI (`sam build && sam deploy`)
- ✅ CloudFormation
- ✅ Terraform
- ✅ AWS CDK

**No additional configuration needed!** The build process ensures `.cs` files are always in your deployment package.
