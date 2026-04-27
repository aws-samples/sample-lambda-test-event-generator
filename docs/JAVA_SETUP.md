# Java Lambda Setup Guide

## Quick Setup for Test Generation

The test generator requires **source code** (`.java` files) to analyze your Lambda function.

## Why Source Code?

- ✅ Better test generation with comments and structure
- ✅ Works across all languages consistently
- ✅ Industry standard for test generation tools

## Maven Setup (Recommended)

Add to your `pom.xml`:

```xml
<build>
    <plugins>
        <!-- Include source files for test generation -->
        <plugin>
            <groupId>org.apache.maven.plugins</groupId>
            <artifactId>maven-resources-plugin</artifactId>
            <version>3.3.0</version>
            <executions>
                <execution>
                    <id>copy-sources</id>
                    <phase>prepare-package</phase>
                    <goals>
                        <goal>copy-resources</goal>
                    </goals>
                    <configuration>
                        <outputDirectory>${project.build.outputDirectory}</outputDirectory>
                        <resources>
                            <resource>
                                <directory>src/main/java</directory>
                                <includes>
                                    <include>**/*.java</include>
                                </includes>
                            </resource>
                        </resources>
                    </configuration>
                </execution>
            </executions>
        </plugin>
    </plugins>
</build>
```

Build and deploy:
```bash
mvn clean package
```

## Manual ZIP (Simple Projects)

```bash
javac Handler.java
zip -r function.zip Handler.java Handler.class
```

## Verification

```bash
jar tf target/your-jar.jar | grep .java
# Should show: Handler.java
```

## Works with SAM/CloudFormation/Terraform

Once you add the maven-resources-plugin to your `pom.xml`, source files are **automatically included** in every build, regardless of deployment method:

- ✅ AWS CLI
- ✅ Lambda Console upload
- ✅ SAM CLI (`sam build && sam deploy`)
- ✅ CloudFormation
- ✅ Terraform
- ✅ AWS CDK

**No additional configuration needed!** The build process ensures `.java` files are always in your JAR.
