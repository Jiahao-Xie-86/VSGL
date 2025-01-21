import torch
import time
import csv
import torch.nn as nn
from utils import wasserstein_loss, visualize_graph_layout, plot_graph_layout
import torch.nn.functional as F
import matplotlib.pyplot as plt
import os

def calculate_f1(preds, labels):
    """
    Function to calculate precision, recall, and F1 score.
    Args:
        preds: Tensor of predicted labels.
        labels: Tensor of true labels.
    Returns:
        precision: Precision score.
        recall: Recall score.
        f1: F1 score.
    """
    tp = (preds * labels).sum().to(torch.float32)  # True positives
    tn = ((1 - preds) * (1 - labels)).sum().to(torch.float32)  # True negatives
    fp = (preds * (1 - labels)).sum().to(torch.float32)  # False positives
    fn = ((1 - preds) * labels).sum().to(torch.float32)  # False negatives

    precision = tp / (tp + fp + 1e-8)
    recall = tp / (tp + fn + 1e-8)
    f1 = 2 * (precision * recall) / (precision + recall + 1e-8)

    return precision.item(), recall.item(), f1.item()

def compute_gradient_penalty(discriminator, real_samples, fake_samples, real_adj, fake_adj, device, lambda_gp=3):
    """
    Computes the gradient penalty for WGAN-GP.

    Args:
        discriminator (nn.Module): The discriminator network.
        real_samples (torch.Tensor): Real graph layout samples.
        fake_samples (torch.Tensor): Fake graph layout samples generated by the generator.
        real_adj (torch.Tensor): Adjacency matrices corresponding to real samples.
        fake_adj (torch.Tensor): Adjacency matrices corresponding to fake samples.
        device (str): The device (CPU or CUDA).
        lambda_gp (float): The gradient penalty weight (commonly set to 10).

    Returns:
        gradient_penalty (torch.Tensor): The gradient penalty term.
    """
    batch_size = real_samples.size(0)
    num_nodes = real_adj.size(1)

    # Random weight term for interpolation between real and fake samples
    alpha_samples = torch.rand(batch_size, 1, 1).to(device)
    alpha_samples = alpha_samples.expand_as(real_samples)

    alpha_adj = torch.rand(batch_size, num_nodes, num_nodes).to(device)  # Expand alpha for adjacency matrices

    # Interpolates between real and fake samples for both node coordinates and adjacency matrices
    interpolated_samples = (alpha_samples * real_samples + (1 - alpha_samples) * fake_samples).requires_grad_(True)
    interpolated_adj = (alpha_adj * real_adj + (1 - alpha_adj) * fake_adj).requires_grad_(True)

    # Discriminator's output on the interpolated samples
    d_interpolates = discriminator(interpolated_samples, interpolated_adj)

    # Compute gradients of the discriminator's output with respect to the interpolated samples
    gradients = torch.autograd.grad(
        outputs=d_interpolates,
        inputs=[interpolated_samples, interpolated_adj],
        grad_outputs=torch.ones(d_interpolates.size()).to(device),
        create_graph=True,
        retain_graph=True,
        only_inputs=True,
        #allow_unused=True  # This prevents the error when some tensors are not used in the graph
    )[0]


    # Compute the gradient norm ||grad||_2
    gradients = gradients.view(batch_size, -1)
    gradient_norm = gradients.norm(2, dim=1)

    # Compute the gradient penalty: (||grad||_2 - 1)^2
    gradient_penalty = lambda_gp * ((gradient_norm - 1) ** 2).mean()

    return gradient_penalty

# ---- Pretrain Generator ----
# ---- Pretrain Both Generator and Discriminator Together ----

def train_gan(generator, discriminator, data_loader, optimizer_g, optimizer_d, device, epochs, output_dir, 
              lambda_gp=1, lambda_rec=100, patience=20):
    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, "metrics_gan.csv")
    save_path_g = os.path.join(output_dir, "best_generator_pretrained.pth")
    save_path_d = os.path.join(output_dir, "best_discriminator_pretrained.pth")
    image_output_dir = os.path.join(output_dir, "gan_pretrain_generated_images")
    os.makedirs(image_output_dir, exist_ok=True)
    """
    Train both the generator and discriminator together.
    """
    #loss_fn = nn.MSELoss()
    best_g_loss = float('inf')
    best_d_loss = float('inf')
    best_d_accuracy = 0

    # Initialize early stopping variables
    g_epochs_without_improvement = 0
    d_epochs_without_improvement = 0
    stop_training_g = False # Flag for stopping generator parameter updates
    stop_training_d = False # Flag for stopping discriminator parameter updates

    # Dictionary to store performance metrics
    metrics = {
        "epoch": [],
        "generator_loss": [],
        "reconstruction_loss":[],
        "discriminator_loss": [],
        "discriminator_accuracy":[]
    }

    with open(log_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['epoch', 'generator_loss', 'reconstruction_loss','discriminator_loss','discriminator_accuracy'])

    for epoch in range(epochs):
        if stop_training_g and stop_training_d:
            print("Early stopping triggered for both generator and discriminator.")
            break

        epoch_start = time.time()  # Start timing the epoch
        generator.train()
        discriminator.train()
        total_g_loss = 0
        total_d_loss = 0
        total_reconstruction_loss = 0

        # Initialize counters for discriminator accuracy
        correct_real = 0
        correct_fake = 0
        total_real = 0
        total_fake = 0

        for batch_idx, (node_coords, adj_matrices,  _) in enumerate(data_loader):
            batch_start = time.time()  # Start timing the batch
            adj_matrices = adj_matrices.to(device)
            node_coords = node_coords.to(device)

            # Generate random noise
            z = torch.randn(node_coords.shape[0], 128).to(device)
            non_zero_nodes_mask = torch.norm(node_coords, dim=2) > 0  # Shape: (96, 46)

            # Count the number of non-zero nodes (num_nodes) for each graph in the batch
            num_nodes = non_zero_nodes_mask.sum(dim=1)  # Shape: (96,) - number of nodes for each graph in the batch

            #generated_coords = generator(z, node_coords, num_nodes)
            generated_coords = generator(z, node_coords, adj_matrices, num_nodes)
            #real_validity = discriminator(node_coords)
            real_validity = discriminator(node_coords, adj_matrices)
            #fake_validity = discriminator(generated_coords.detach())
            fake_validity = discriminator(generated_coords.detach(),adj_matrices)  # Detach to avoid updating generator

            # print(f"Real Validity - Mean: {real_validity.mean().item():.4f}, Std: {real_validity.std().item():.4f}")
            # print(f"Fake Validity - Mean: {fake_validity.mean().item():.4f}, Std: {fake_validity.std().item():.4f}")

            # Train Discriminator if not stopped
            if not stop_training_d:
                # Discriminator loss (Wasserstein Loss)
                discriminator_loss = wasserstein_loss(real_validity, fake_validity)
                # Compute gradient penalty
                gp = compute_gradient_penalty(discriminator, node_coords, generated_coords, adj_matrices, adj_matrices, device, lambda_gp)
                # Add gradient penalty to discriminator loss
                d_loss = discriminator_loss + gp

                optimizer_d.zero_grad()
                d_loss.backward(retain_graph=True)  # Only retain the graph for the discriminator's backward pass

                # Apply gradient clipping to discriminator
                # torch.nn.utils.clip_grad_norm_(discriminator.parameters(), max_norm=5.0)
                optimizer_d.step()
                total_d_loss += d_loss.item()

            # ---- Update Discriminator Accuracy Counters ----
            # Concatenate real and fake scores
            all_validity = torch.cat([real_validity, fake_validity], dim=0)
            # Compute dynamic threshold (using median)
            threshold = all_validity.median().item()
            # Make predictions based on threshold
            real_predictions = (real_validity > threshold).float()
            fake_predictions = (fake_validity <= threshold).float()

            # Update counters
            correct_real += real_predictions.sum().item()
            correct_fake += fake_predictions.sum().item()
            total_real += real_predictions.numel()
            total_fake += fake_predictions.numel()

             # Train Generator if not stopped
            if not stop_training_g:
            
                #fake_validity = discriminator(generated_coords)
                fake_validity = discriminator(generated_coords,adj_matrices)
                generate_loss = -torch.mean(fake_validity)  # Negative critic score on fake samples (WGAN)

                # Reconstruction loss between generated and real layouts
                reconstruction_loss = F.mse_loss(generated_coords, node_coords)

                # Total generator loss
                g_loss = generate_loss + lambda_rec * reconstruction_loss
                optimizer_g.zero_grad()
                g_loss.backward()

                # Apply gradient clipping to generator
                # torch.nn.utils.clip_grad_norm_(generator.parameters(), max_norm=5.0)
                optimizer_g.step()
                total_g_loss += g_loss.item()
                total_reconstruction_loss += reconstruction_loss.item()
        

            if batch_idx % 30 == 0:  # Visualize and save every 10 batches
                for i, layout in enumerate(generated_coords[:3]):  # Visualize the first 3 generated layouts
                    # Generate the image using your visualize_graph_layout function
                    img = plot_graph_layout(
                        layout[:num_nodes[i].item()].detach().cpu().numpy(),
                        adj_matrix=adj_matrices[i].detach().cpu().numpy(),
                        return_tensor=False  # Set return_tensor to False to get the PIL image instead of a tensor
                    )

                    # Construct the filename using the epoch, batch index, and image index
                    image_filename = f"epoch_{epoch}_batch_{batch_idx}_image_{i}.png"
                    
                    # Save the image in the specified directory
                    img.save(os.path.join(image_output_dir, image_filename))

                    # # Optionally, you can still display the image (remove this if not needed)
                    # plt.imshow(img)
                    # plt.show()  # Show the image

            # Print batch processing time
            print(f"Batch {batch_idx + 1}/{len(data_loader)} processed in {time.time() - batch_start:.2f} seconds")

        avg_g_loss = total_g_loss / len(data_loader)
        avg_d_loss = total_d_loss / len(data_loader)
        avg_reconstruction_loss = total_reconstruction_loss / len(data_loader)

         # Calculate discriminator accuracy
        if total_real + total_fake > 0:
            total_correct = correct_real + correct_fake
            total_samples = total_real + total_fake
            discriminator_accuracy = (total_correct / total_samples) * 100
        else:
            discriminator_accuracy = 0.0

        print(f"Epoch {epoch + 1}/{epochs} completed in {time.time() - epoch_start:.2f} seconds")
        print(f"Epoch {epoch + 1}/{epochs}, Generator Loss: {avg_g_loss:.4f},  Discriminator Loss: {avg_d_loss:.4f}, Reconstruction Loss: {avg_reconstruction_loss:.4f}")
        print(f"Discriminator Accuracy: {discriminator_accuracy:.4f}%")

        # Early stopping for generator
        if avg_g_loss < best_g_loss:  # Check if the generator loss is improving
            best_g_loss = avg_g_loss
            g_epochs_without_improvement = 0
            torch.save(generator.state_dict(), save_path_g)
            print(f"Best Generator model saved with loss: {best_g_loss:.4f}")
        else:
            g_epochs_without_improvement += 1
            print(f"Generator has not improved for {g_epochs_without_improvement} epochs.")
            if g_epochs_without_improvement >= patience:
                print(f"Stopping generator updates.")
                stop_training_g = True

        # Early stopping for discriminator
        if avg_d_loss < best_d_loss:  # Check if the discriminator loss is improving
            best_d_loss = avg_d_loss
            d_epochs_without_improvement = 0
            torch.save(discriminator.state_dict(), save_path_d)
            print(f"Best Discriminator model saved with loss: {best_d_loss:.4f}")
        else:
            d_epochs_without_improvement += 1
            print(f"Discriminator has not improved for {d_epochs_without_improvement} epochs.")
            if d_epochs_without_improvement >= patience:
                print(f"Stopping discriminator updates.")
                stop_training_d = True

        # if discriminator_accuracy > best_d_accuracy:
        #     best_d_accuracy = discriminator_accuracy
        #     torch.save(discriminator.state_dict(), save_path_d)
        #     print(f"Best Discriminator model saved with accuracy: {best_d_accuracy:.4f}%")
        

        # Log metrics for this epoch
        metrics["epoch"].append(epoch + 1)
        metrics["generator_loss"].append(avg_g_loss)
        metrics["reconstruction_loss"].append(avg_reconstruction_loss)
        metrics["discriminator_loss"].append(avg_d_loss)
        metrics["discriminator_accuracy"].append(discriminator_accuracy)
        
        # Append metrics to the CSV file
        with open(log_path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([epoch + 1, avg_g_loss,avg_reconstruction_loss, avg_d_loss, discriminator_accuracy])



def train_combined_cgan(generator, discriminator, classifier, train_loader, val_loader, test_loader, 
                        optimizer_g, optimizer_d, optimizer_c, scheduler_g, scheduler_d, scheduler_c, criterion, 
                        device, epochs=100, patience=10, output_dir=".", lambda_gp=5, alpha=30, num_z_samples=10):
    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, "metrics_combined_train.csv")
    save_path = os.path.join(output_dir, "best_model.pth")
    image_output_dir = os.path.join(output_dir, "generated_images")
    os.makedirs(image_output_dir, exist_ok=True)
    
    best_accuracy = 0  # Initialize best accuracy
    epochs_without_improvement = 0  # Counter for early stopping

    # Dictionary to store performance metrics
    metrics = {
        "epoch": [],
        "generator_loss": [],
        "discriminator_loss": [],
        "classifier_loss": [],
        "train_accuracy": [],
        "train_f1": [],
        "val_accuracy": [],
        "val_f1": [],
        "test_accuracy": [],
        "test_f1": []
    }

    # Create a CSV file and write the headers
    with open(log_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["epoch", "generator_loss", "discriminator_loss", "classifier_loss", "train_accuracy", 
                         "train_f1", "val_accuracy", "val_f1", "test_accuracy", "test_f1"])
        
    
    # ---- Evaluate the performance of the untrained (initial) model ----
    print("Evaluating initial model performance...")
    train_accuracy, train_f1 = evaluate_model(generator, classifier, train_loader, device)
    val_accuracy, val_f1 = evaluate_model(generator, classifier, val_loader, device)
    test_accuracy, test_f1 = evaluate_model(generator, classifier, test_loader, device)

    # Log the initial metrics as "epoch 0"
    metrics["epoch"].append(0)
    metrics["generator_loss"].append(None)  # No generator loss yet
    metrics["discriminator_loss"].append(None)  # No discriminator loss yet
    metrics["classifier_loss"].append(None)  # No classifier loss yet
    metrics["train_accuracy"].append(train_accuracy)
    metrics["train_f1"].append(train_f1)
    metrics["val_accuracy"].append(val_accuracy)
    metrics["val_f1"].append(val_f1)
    metrics["test_accuracy"].append(test_accuracy)
    metrics["test_f1"].append(test_f1)

    # Append initial metrics to the CSV file
    with open(log_path, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([0, None, None, None, train_accuracy, train_f1, val_accuracy, val_f1, test_accuracy, test_f1])

    print(f"Initial Model - Train Accuracy: {train_accuracy:.2f}%, Validation Accuracy: {val_accuracy:.2f}%, Test Accuracy: {test_accuracy:.2f}%")
    print(f"Initial Model - Train F1: {train_f1:.4f}, Validation F1: {val_f1:.4f}, Test F1: {test_f1:.4f}")
    

    for epoch in range(epochs):
        epoch_start = time.time()  # Start timing the epoch
        generator.train()
        discriminator.train()
        classifier.train()

        total_g_loss = 0
        total_d_loss = 0
        total_c_loss = 0
        correct_train = 0
        total_train = 0  # For calculating training accuracy
        total_f1_train = 0  # For calculating average F1 score for training

        # Training phase
        for batch_idx, (graph_layouts, adj_matrices, labels) in enumerate(train_loader):
            batch_start = time.time()  # Start timing the batch
            graph_layouts = graph_layouts.float().to(device)  # Move data to device
            adj_matrices = adj_matrices.float().to(device)  # Adjacency matrices
            labels = labels.to(device)  # Move labels to device

             # Get the number of nodes from the adjacency matrix (which should match graph_layouts)
            #num_nodes = adj_matrices.size(1)
            # Calculate the mask of non-zero coordinates (true if norm > 0)
            non_zero_nodes_mask = torch.norm(graph_layouts, dim=2) > 0  # Shape: (96, 46)

            # Count the number of non-zero nodes (num_nodes) for each graph in the batch
            num_nodes = non_zero_nodes_mask.sum(dim=1)  # Shape: (96,) - number of nodes for each graph in the batch

            # Update Discriminator
            # z = torch.randn(graph_layouts.shape[0], 128).to(device)  # Random noise for GAN on device
            # # Pass the input graph and noise to the conditional generator
            # #generated_layouts = generator(z, graph_layouts, num_nodes=num_nodes)
            # generated_layouts = generator(z, graph_layouts,adj_matrices, num_nodes=num_nodes)

            # # Train Discriminator
            real_validity = discriminator(graph_layouts,adj_matrices)
            # fake_validity = discriminator(generated_layouts.detach(),adj_matrices)
            # Accumulate fake validity from multiple z samples
            fake_validities = []
            for _ in range(num_z_samples):
                z = torch.randn(graph_layouts.shape[0], 128).to(device)
                generated_layouts = generator(z, graph_layouts, adj_matrices, num_nodes=num_nodes)
                # Detach so G won't be updated in this pass
                fv = discriminator(generated_layouts.detach(), adj_matrices)
                fake_validities.append(fv)
            
            # Average across z-samples
            fake_validity = torch.stack(fake_validities, dim=0).mean(dim=0)
             # WGAN Discriminator (Critic) loss
            discriminator_loss = wasserstein_loss(real_validity, fake_validity)

            # Add gradient penalty
            gp = compute_gradient_penalty(discriminator, graph_layouts, generated_layouts, adj_matrices, adj_matrices, device, lambda_gp)
            d_loss = discriminator_loss + gp
            optimizer_d.zero_grad()

             # Check Discriminator Gradients
            d_loss.backward(retain_graph=True)  # Only retain the graph for the discriminator's backward pass
            
            # Apply gradient clipping to discriminator
            # torch.nn.utils.clip_grad_norm_(discriminator.parameters(), max_norm=5.0)
            optimizer_d.step()# Update discriminator

            total_d_loss += d_loss.item()

            # ---- Update Generator ---- Update Generator with multiple z-samples
            # We'll accumulate the generator's adversarial loss & classifier loss
            adv_losses = []
            clf_losses = []

            for _ in range(num_z_samples):
                z = torch.randn(graph_layouts.shape[0], 128).to(device)
                gen_layouts = generator(z, graph_layouts, adj_matrices, num_nodes=num_nodes)

                # Adversarial loss for generator
                f_validity = discriminator(gen_layouts, adj_matrices)
                adv_loss = -torch.mean(f_validity)  # WGAN-G: maximize D output => negative sign

                # Convert layouts to images for classification feedback
                # (visualize_graph_layout is presumably your differentiable layout->image code)
                images = []
                for i, layout in enumerate(generated_layouts):
                    adj_matrix = adj_matrices[i]  # Get corresponding adjacency matrix
                    img = visualize_graph_layout(layout[:num_nodes[i]], adj_matrix=adj_matrix, size=(224, 224))  # Get image tensor directly
                    images.append(img)
                images = torch.stack(images).to(device)  # Concatenate into a batch tensor

                

                # Classifier loss
                outputs = classifier(images) # Classifier predictions
                c_loss = criterion(outputs, labels) # Cross-entropy loss for classification

                adv_losses.append(adv_loss)
                clf_losses.append(c_loss)
            

            # Average across the z-samples
            mean_adv_loss = torch.mean(torch.stack(adv_losses))
            mean_clf_loss = torch.mean(torch.stack(clf_losses))

            # Weighted sum
            g_loss = mean_adv_loss + alpha * mean_clf_loss


            #fake_validity = discriminator(generated_layouts,adj_matrices)

            # WGAN Generator loss (negative critic score on fake samples)
            # g_loss = -torch.mean(fake_validity)

            # ---- Add Classifier's feedback to Generator ----
            # images = []
            # for i, layout in enumerate(generated_layouts):
            #     adj_matrix = adj_matrices[i].detach().cpu().numpy()  # Get the corresponding adjacency matrix
            #     img = visualize_graph_layout(layout[:num_nodes[i]].detach().cpu().numpy(), adj_matrix=adj_matrix, return_tensor=True)   
            #     images.append(img)
            # images = torch.stack(images).to(device)

            # # Use the differentiable `visualize_graph_layout` for layout-to-image conversion
            # images = []
            # for i, layout in enumerate(generated_layouts):
            #     adj_matrix = adj_matrices[i]  # Get corresponding adjacency matrix
            #     img = visualize_graph_layout(layout[:num_nodes[i]], adj_matrix=adj_matrix, size=(224, 224))  # Get image tensor directly
            #     images.append(img)
            # images = torch.stack(images).to(device)  # Concatenate into a batch tensor

            # # Classify the generated layouts
            # outputs = classifier(images) # Classifier predictions
            # c_loss = criterion(outputs, labels)  # Cross-entropy loss for classification

            # # Combine the generator's adversarial loss and classification loss
            # g_loss = g_loss + alpha * c_loss  # alpha controls the influence of the classifier loss

            # Train Generator
            optimizer_g.zero_grad()
            
            #g_loss = criterion(fake_validity, torch.ones_like(fake_validity).to(device))
            g_loss.backward(retain_graph=True)

            # Apply gradient clipping to generator
            # torch.nn.utils.clip_grad_norm_(generator.parameters(), max_norm=5.0)
            optimizer_g.step()
            total_g_loss += g_loss.item()

            # ---- Update Classifier ----
            #outputs = classifier(images)
            c_loss = mean_clf_loss  # average of the loops
            optimizer_c.zero_grad()
            c_loss.backward()
            optimizer_c.step()
            total_c_loss += c_loss.item()

            # Calculate training accuracy for this batch
            _, predicted_train = torch.max(outputs, 1)
            correct_train += (predicted_train == labels).sum().item()
            total_train += labels.size(0)  # Increment total number of samples

            precision, recall, f1_score = calculate_f1(predicted_train, labels)
            total_f1_train += f1_score


            # Print batch processing time
            print(f"Batch {batch_idx + 1}/{len(train_loader)} processed in {time.time() - batch_start:.2f} seconds")

        
            if batch_idx % 10 == 0:  # Visualize and save every 10 batches
                for i, layout in enumerate(generated_layouts[:3]):  # Visualize the first 3 generated layouts
                    # Generate the image using your visualize_graph_layout function
                    img = plot_graph_layout(
                        layout[:num_nodes[i]].detach().cpu().numpy(),
                        adj_matrix=adj_matrices[i].detach().cpu().numpy(),
                        return_tensor=False  # Set return_tensor to False to get the PIL image instead of a tensor
                    )

                    # Construct the filename using the epoch, batch index, and image index
                    image_filename = f"epoch_{epoch}_batch_{batch_idx}_image_{i}.png"
                    
                    # Save the image in the specified directory
                    img.save(os.path.join(image_output_dir, image_filename))

                    # Optionally, you can still display the image (remove this if not needed)
                    # plt.imshow(img)
                    # plt.show()  # Show the image
            
            

        # Calculate training accuracy and F1 score for this epoch
        train_accuracy = 100 * correct_train / total_train
        avg_f1_train = total_f1_train / len(train_loader)

        # Validation phase
        val_accuracy, val_f1 = evaluate_model(generator, classifier, val_loader, device)

        # Test phase (added after every epoch)
        test_accuracy, test_f1 = evaluate_model(generator, classifier, test_loader, device)

        # Print epoch statistics
        avg_g_loss = total_g_loss / len(train_loader)
        avg_d_loss = total_d_loss / len(train_loader)
        avg_c_loss = total_c_loss / len(train_loader)

        # Step the schedulers
        scheduler_g.step()
        scheduler_d.step()
        scheduler_c.step()
        print(f"Epoch {epoch + 1}/{epochs} completed in {time.time() - epoch_start:.2f} seconds")
        print(f"Epoch {epoch+1}, Generator LR: {scheduler_g.get_last_lr()[0]}, Discriminator LR: {scheduler_d.get_last_lr()[0]}, Classifier LR: {scheduler_c.get_last_lr()[0]}")
        print(f"Generator Loss: {avg_g_loss:.4f}, Discriminator Loss: {avg_d_loss:.4f}, Classifier Loss: {avg_c_loss:.4f}")
        print(f"Training Accuracy: {train_accuracy:.2f}%, Training F1: {avg_f1_train:.4f}")
        print(f"Validation Accuracy: {val_accuracy:.2f}%, Validation F1: {val_f1:.4f}")
        print(f"Test Accuracy: {test_accuracy:.2f}%, Test F1: {test_f1:.4f}")

        # Log metrics for this epoch
        metrics["epoch"].append(epoch + 1)
        metrics["generator_loss"].append(avg_g_loss)
        metrics["discriminator_loss"].append(avg_d_loss)
        metrics["classifier_loss"].append(avg_c_loss)
        metrics["train_accuracy"].append(train_accuracy)
        metrics["train_f1"].append(avg_f1_train)
        metrics["val_accuracy"].append(val_accuracy)
        metrics["val_f1"].append(val_f1)
        metrics["test_accuracy"].append(test_accuracy)
        metrics["test_f1"].append(test_f1)

        # Append metrics to CSV file
        with open(log_path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([epoch + 1, avg_g_loss, avg_d_loss, avg_c_loss, train_accuracy, avg_f1_train, val_accuracy, val_f1, test_accuracy, test_f1])

        # Check for improvement in accuracy
        if val_accuracy > best_accuracy :
            best_accuracy = val_accuracy
            epochs_without_improvement = 0  # Reset the counter if accuracy improves

            # Save the best model
            torch.save({
                'generator_state_dict': generator.state_dict(),
                'discriminator_state_dict': discriminator.state_dict(),
                'classifier_state_dict': classifier.state_dict(),
                'optimizer_g_state_dict': optimizer_g.state_dict(),
                'optimizer_d_state_dict': optimizer_d.state_dict(),
                'optimizer_c_state_dict': optimizer_c.state_dict(),
                'epoch': epoch,
                'val_accuracy': val_accuracy,
                'test_accuracy': test_accuracy
            }, save_path)
            print(f"Best model saved with validation accuracy: {val_accuracy:.2f}%")
        else:
            epochs_without_improvement += 1
            print(f"Epochs without improvement: {epochs_without_improvement}")

        # Early stopping condition
        if epochs_without_improvement >= patience:
            print(f"Early stopping triggered. No improvement for {patience} consecutive epochs.")
            break

    # After training, save the metrics dictionary as a backup
    torch.save(metrics, 'metrics.pth')


# def evaluate_model(generator, classifier, test_loader, device):
#     generator.eval()
#     classifier.eval()
#     correct = 0
#     total = 0
#     total_f1 = 0
#     with torch.no_grad():
#         for graph_layouts, adj_matrices, labels in test_loader:  # Unpack three values now
#             graph_layouts = graph_layouts.float().to(device)
#             adj_matrices = adj_matrices.float().to(device)
#             labels = labels.to(device)

#             z = torch.randn(graph_layouts.shape[0], 128).to(device)  # Random noise on device
            
#             non_zero_nodes_mask = torch.norm(graph_layouts, dim=2) > 0  # Shape: (96, 46)

#             # Count the number of non-zero nodes (num_nodes) for each graph in the batch
#             num_nodes = non_zero_nodes_mask.sum(dim=1)  # Shape: (96,) - number of nodes for each graph in the batch
            
#             # Generate layouts by passing the correct number of nodes and the input graph
#             #generated_layouts = generator(z, graph_layouts, num_nodes=num_nodes)
#             generated_layouts = generator(z, graph_layouts, adj_matrices, num_nodes=num_nodes)

#             # Convert layouts to images for classification
#             # images = []
#             # for i, layout in enumerate(generated_layouts):
#             #     adj_matrix = adj_matrices[i].detach().cpu().numpy()  # Get the corresponding adjacency matrix
#             #     img = visualize_graph_layout(layout[:num_nodes[i]].detach().cpu().numpy(), adj_matrix=adj_matrix, return_tensor=True)
#             #     images.append(img)
#             # images = torch.stack(images).to(device)

#             # Use the differentiable `visualize_graph_layout` for layout-to-image conversion
#             images = []
#             for i, layout in enumerate(generated_layouts):
#                 adj_matrix = adj_matrices[i]  # Get corresponding adjacency matrix
#                 img = visualize_graph_layout(layout[:num_nodes[i]], adj_matrix=adj_matrix, size=(224, 224))  # Get image tensor directly
#                 images.append(img)
#             images = torch.stack(images).to(device)  # Concatenate into a batch tensor

#             # Classify using ResNet-50
#             outputs = classifier(images)

#             _, predicted = torch.max(outputs, 1)
#             total += labels.size(0)
#             correct += (predicted == labels).sum().item()

#             # Calculate F1 score
#             precision, recall, f1_score = calculate_f1(predicted, labels)
#             total_f1 += f1_score

#     accuracy = 100 * correct / total
#     avg_f1 = total_f1 / len(test_loader)
#     #print(f'Test Accuracy: {accuracy:.2f}%, F1 Score: {avg_f1:.4f}')
#     return accuracy, avg_f1


def evaluate_model(generator, classifier, test_loader, device, num_z_samples=5):
    generator.eval()
    classifier.eval()
    correct = 0
    total = 0
    total_f1 = 0
    with torch.no_grad():
        for graph_layouts, adj_matrices, labels in test_loader:  # Unpack three values now
            graph_layouts = graph_layouts.float().to(device)
            adj_matrices = adj_matrices.float().to(device)
            labels = labels.to(device)

            # z = torch.randn(graph_layouts.shape[0], 128).to(device)  # Random noise on device
            
            non_zero_nodes_mask = torch.norm(graph_layouts, dim=2) > 0  # Shape: (96, 46)

            # Count the number of non-zero nodes (num_nodes) for each graph in the batch
            num_nodes = non_zero_nodes_mask.sum(dim=1)  # Shape: (96,) - number of nodes for each graph in the batch
            
            batch_size = graph_layouts.size(0)
            # Generate layouts by passing the correct number of nodes and the input graph
            #generated_layouts = generator(z, graph_layouts, num_nodes=num_nodes)
            # generated_layouts = generator(z, graph_layouts, adj_matrices, num_nodes=num_nodes)

            accum_logits = torch.zeros(batch_size, 2, device=device)
            # # Use the differentiable `visualize_graph_layout` for layout-to-image conversion
            # images = []
            # for i, layout in enumerate(generated_layouts):
            #     adj_matrix = adj_matrices[i]  # Get corresponding adjacency matrix
            #     img = visualize_graph_layout(layout[:num_nodes[i]], adj_matrix=adj_matrix, size=(224, 224))  # Get image tensor directly
            #     images.append(img)
            # images = torch.stack(images).to(device)  # Concatenate into a batch tensor

            # # Classify using ResNet-50
            # outputs = classifier(images)

            for _ in range(num_z_samples):
                z = torch.randn(graph_layouts.shape[0], 128).to(device)
                gen_layouts = generator(z, graph_layouts, adj_matrices, num_nodes = num_nodes)

                # Convert layouts to images
                images = []
                for i, layout in enumerate(gen_layouts):
                    adj_matrix = adj_matrices[i]  # Get corresponding adjacency matrix
                    img = visualize_graph_layout(layout[:num_nodes[i]], adj_matrix=adj_matrix, size=(224, 224))  # Get image tensor directly
                    images.append(img)
                images = torch.stack(images).to(device)  # Concatenate into a batch tensor


                # Classifier forward pass
                outputs = classifier(images)  # shape: (batch_size, num_classes=2)

                # We accumulate raw logits (before softmax)
                accum_logits += outputs

            # Average the logits over num_z_samples
            accum_logits /= float(num_z_samples)

            _, predicted = torch.max(accum_logits, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()

            # Calculate F1 score
            precision, recall, f1_score = calculate_f1(predicted, labels)
            total_f1 += f1_score

    accuracy = 100 * correct / total
    avg_f1 = total_f1 / len(test_loader)
    #print(f'Test Accuracy: {accuracy:.2f}%, F1 Score: {avg_f1:.4f}')
    return accuracy, avg_f1